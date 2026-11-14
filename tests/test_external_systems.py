"""
External systems detected from third-party imports: what counts, what doesn't, how the
config overrides it, and where it shows up (L0 in every format, the diff, MCP, init).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from archilens.analyzers.diff import compute_diff
from archilens.analyzers.external import CATALOG, detect_external_systems, match_system
from archilens.cli import main
from archilens.config import ArchiLensConfig, ExternalSystem
from archilens.engine import analyze_repository
from archilens.generators.d2 import D2Generator
from archilens.generators.mermaid import generate_system_context
from archilens.generators.plantuml import PlantUMLGenerator
from archilens.models import ArchSnapshot, DetectedSystem


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def _detected(repo: Path, config: ArchiLensConfig | None = None) -> dict[str, DetectedSystem]:
    snap = analyze_repository(repo, config or ArchiLensConfig(), use_ai=False)
    return {s.name: s for s in snap.detected_systems}


def _name(imported: str, language: str) -> str | None:
    hit = match_system(imported, language)
    return hit[0].name if hit else None


class TestCatalog:
    @pytest.mark.parametrize(
        "imported, language, expected",
        [
            ("psycopg2.extras", "python", "PostgreSQL"),
            ("google.cloud.storage", "python", "Google Cloud Storage"),
            ("django.db.models", "python", "SQL database"),
            ("pg", "javascript", "PostgreSQL"),
            ("@aws-sdk/client-s3", "typescript", "Amazon S3"),
            ("@aws-sdk/client-lambda", "typescript", "AWS"),  # no entry of its own: the generic root
            ("@sentry/node", "vue", "Sentry"),
            ("org.springframework.data.redis.core.RedisTemplate", "java", "Redis"),
            ("software.amazon.awssdk.services.sqs.SqsClient", "java", "Amazon SQS"),
            ("jakarta.persistence.Entity", "java", "SQL database"),
            ("github.com/jackc/pgx/v5/pgxpool", "go", "PostgreSQL"),
            ("github.com/redis/go-redis/v9", "go", "Redis"),
            ("github.com/aws/aws-sdk-go-v2/service/s3", "go", "Amazon S3"),
            ("database/sql", "go", "SQL database"),
            ("go.mongodb.org/mongo-driver/mongo", "go", "MongoDB"),
        ],
    )
    def test_known_imports(self, imported, language, expected):
        assert _name(imported, language) == expected

    @pytest.mark.parametrize(
        "imported, language",
        [
            ("requests", "python"),  # an ordinary library is not a system
            ("os.path", "python"),
            ("redis_lock", "python"),  # "redis" must match as a whole root, not a prefix of a name
            ("pgx", "javascript"),
            ("lodash", "typescript"),
            ("redis-commander", "javascript"),
            ("com.acme.shop.Orders", "java"),
            ("github.com/lib/pqueue", "go"),
            ("go.mongodb.org/mongo-driver/bson", "go"),  # a serialization format, not a MongoDB server
            ("go.mongodb.org/mongo-driver/v2/bson/primitive", "go"),
            ("psycopg2", "ruby"),  # no catalog for the language
        ],
    )
    def test_everything_else_is_not_a_system(self, imported, language):
        assert _name(imported, language) is None

    def test_every_entry_has_a_supported_type(self):
        kinds = {system.kind for ecosystem in CATALOG.values() for system in ecosystem.values() if system}
        assert kinds <= {"database", "cache", "queue", "storage", "external_api"}


class TestDetection:
    def test_generic_yields_to_specific_of_the_same_family(self):
        found = detect_external_systems([
            ("app/db", "sqlalchemy.orm", "python"),
            ("app/db", "psycopg2", "python"),
            ("app/files", "boto3", "python"),
        ])
        assert [(s.name, s.generic) for s in found] == [("AWS", True), ("PostgreSQL", False)]

    def test_generic_alone_is_reported(self):
        assert [s.name for s in detect_external_systems([("app", "sqlalchemy", "python")])] == ["SQL database"]

    def test_evidence_is_collected_across_modules(self):
        found = detect_external_systems([
            ("billing", "psycopg2", "python"), ("orders", "asyncpg.pool", "python"), ("orders", "psycopg2", "python"),
        ])
        assert found[0].packages == ["asyncpg", "psycopg2"] and found[0].modules == ["billing", "orders"]

    def test_used_by_reads_well(self):
        system = DetectedSystem(name="x", type="database", modules=["src/main/java/com/acme/owner", "<root>"])
        assert system.used_by() == "used by owner and the root package"
        many = DetectedSystem(name="x", type="database", modules=["a", "b", "c", "d", "e"])
        assert many.used_by() == "used by a, b, c and 2 more"

    def test_what_the_config_declares_is_not_repeated(self):
        unresolved = [("a", "psycopg2", "python"), ("a", "stripe", "python"), ("a", "redis", "python")]
        left = detect_external_systems(unresolved, [("Postgres", "database"), ("Stripe API", "external_api")])
        assert [s.name for s in left] == ["Redis"]

    def test_a_declared_database_covers_the_generic_sql_database_only(self):
        unresolved = [("a", "sqlalchemy", "python"), ("a", "pymongo", "python")]
        left = detect_external_systems(unresolved, [("Orders DB", "database")])
        assert [s.name for s in left] == ["MongoDB"]  # a specific system is still worth showing


class TestEndToEnd:
    def test_python_and_a_local_package_named_like_a_library(self, tmp_path):
        repo = _repo(tmp_path, {
            "app/orders/service.py": "import stripe\nimport requests\nfrom redis import Redis\nimport os\n",
            "app/billing/charge.py": "import stripe\nfrom app.kafka.bus import publish\n",
            "app/kafka/bus.py": "def publish(): pass\n",  # the project's own package, not Kafka
            "app/orders/tests/test_service.py": "import pymongo\n",  # tests don't count
        })
        found = _detected(repo)
        assert set(found) == {"Redis", "Stripe"}
        assert found["Stripe"].modules == ["app/billing", "app/orders"]

    def test_typescript_java_and_go(self, tmp_path):
        repo = _repo(tmp_path, {
            "web/src/api.ts": 'import { S3Client } from "@aws-sdk/client-s3";\nimport Stripe from "stripe";\nimport _ from "lodash";\n',
            "svc/src/main/java/com/acme/Repo.java": (
                "package com.acme;\nimport org.apache.kafka.clients.producer.KafkaProducer;\n"
                "import jakarta.persistence.Entity;\nimport org.postgresql.Driver;\nclass Repo {}\n"
            ),
            "worker/go.mod": "module example.com/worker\n",
            "worker/main.go": (
                'package main\n\nimport (\n\t"fmt"\n\t"github.com/redis/go-redis/v9"\n'
                '\t"github.com/go-sql-driver/mysql"\n)\n\nfunc main() { fmt.Println(redis.Nil, mysql.ErrInvalidConn) }\n'
            ),
        })
        # jakarta.persistence (generic SQL) yields to the PostgreSQL and MySQL drivers
        assert set(_detected(repo)) == {"Amazon S3", "Kafka", "MySQL", "PostgreSQL", "Redis", "Stripe"}

    def test_detection_can_be_turned_off(self, tmp_path):
        repo = _repo(tmp_path, {"app/a.py": "import stripe\n"})
        config = ArchiLensConfig()
        config.analysis.detect_external_systems = False
        assert _detected(repo, config) == {}

    def test_config_key(self, tmp_path):
        from archilens.config import load_config

        _repo(tmp_path, {"app/a.py": "import stripe\n", ".archilens.yml": "analysis:\n  detect_external_systems: false\n"})
        assert load_config(tmp_path).analysis.detect_external_systems is False
        (tmp_path / ".archilens.yml").write_text("analysis:\n  module_depth: 2\n", encoding="utf-8")
        assert load_config(tmp_path).analysis.detect_external_systems is True


def _snapshot() -> tuple[ArchSnapshot, ArchiLensConfig]:
    config = ArchiLensConfig()
    config.external_systems = [ExternalSystem(name="Fraud service", type="external_api", description="Scores orders")]
    snap = ArchSnapshot(project_name="Shop", detected_systems=[
        DetectedSystem(name="PostgreSQL", type="database", packages=["psycopg2"], modules=["app/orders", "app/billing"]),
        DetectedSystem(name="Amazon S3", type="storage", packages=["@aws-sdk/client-s3"], modules=["web"]),
        DetectedSystem(name="Stripe", type="external_api", packages=["stripe"], modules=["app/billing"]),
    ])
    return snap, config


class TestDiagrams:
    def test_mermaid_draws_declared_then_detected_with_a_table(self):
        snap, config = _snapshot()
        out = generate_system_context(snap, config)
        assert 'Fraud_service["Fraud service<br/><small>Scores orders</small>"]:::external' in out
        assert 'PostgreSQL[("PostgreSQL<br/><small>used by orders and billing</small>")]:::database' in out
        assert 'Amazon_S3[("Amazon S3<br/><small>used by web</small>")]:::database' in out  # storage is a cylinder
        assert 'Stripe["Stripe<br/><small>used by billing</small>"]:::external' in out
        assert out.count("SYSTEM --> ") == 4
        assert "| Fraud service | external api | declared in .archilens.yml |  |" in out
        assert "| PostgreSQL | database | detected from imports of psycopg2 | `app/orders`, `app/billing` |" in out
        assert "analysis.detect_external_systems: false" in out

    def test_no_systems_no_table(self):
        out = generate_system_context(ArchSnapshot(project_name="Shop"), ArchiLensConfig())
        assert "## External Systems" not in out

    def test_d2_and_plantuml(self):
        snap, config = _snapshot()
        d2 = D2Generator(config).system_context(snap)
        assert 'ext_0_Fraud_service: "Fraud service"' in d2 and 'ext_1_PostgreSQL: "PostgreSQL"' in d2
        assert d2.count("shape: cylinder") == 2 and "used by orders and billing" in d2
        puml = PlantUMLGenerator(config).system_context(snap)
        assert 'database "PostgreSQL" as' in puml and 'database "Amazon S3" as' in puml
        assert 'rectangle "Stripe" as' in puml and "used by billing" in puml


class TestDiff:
    def _snap(self, *systems: DetectedSystem) -> ArchSnapshot:
        return ArchSnapshot(project_name="Shop", detected_systems=list(systems))

    def test_a_change_that_starts_using_a_system_is_flagged(self):
        base = self._snap(DetectedSystem(name="PostgreSQL", type="database", modules=["orders"]))
        head = self._snap(
            DetectedSystem(name="PostgreSQL", type="database", modules=["orders", "billing"]),
            DetectedSystem(name="Stripe", type="external_api", modules=["billing"]),
        )
        entries = {e.description: e for e in compute_diff(base, head).entries}
        added = entries["New external system: Stripe (external API), used by billing"]
        assert (added.change_type, added.entity_type, added.severity) == ("added", "external", "warning")
        assert entries["PostgreSQL is now also used by billing"].change_type == "modified"

    def test_a_system_no_longer_used(self):
        base = self._snap(DetectedSystem(name="Redis", type="cache", modules=["orders"]))
        entries = compute_diff(base, self._snap()).entries
        assert [(e.change_type, e.description, e.severity) for e in entries] == [
            ("removed", "External system no longer used: Redis", "info")
        ]

    def test_unchanged_systems_say_nothing(self):
        system = DetectedSystem(name="Redis", type="cache", modules=["orders"])
        assert compute_diff(self._snap(system), self._snap(system)).entries == []


def test_init_reports_and_records_what_it_detected(tmp_path):
    _repo(tmp_path, {"app/orders/a.py": "import psycopg2\nimport stripe\n", "app/billing/b.py": "x = 1\n"})
    out = " ".join(CliRunner().invoke(main, ["init", "--repo", str(tmp_path)]).output.split())
    assert "Detected external systems: PostgreSQL, Stripe." in out
    written = (tmp_path / ".archilens.yml").read_text(encoding="utf-8")
    assert "# Detected in this repository: PostgreSQL, Stripe." in written
    assert "{{" not in written


def test_init_with_nothing_detected(tmp_path):
    _repo(tmp_path, {"app/a.py": "x = 1\n"})
    result = CliRunner().invoke(main, ["init", "--repo", str(tmp_path)])
    assert "Detected external systems" not in result.output
    assert "# None detected in this repository yet." in (tmp_path / ".archilens.yml").read_text(encoding="utf-8")


def test_mcp_l0_lists_detected_systems(tmp_path, monkeypatch):
    pytest.importorskip("mcp.server.fastmcp", reason="requires the 'mcp' extra (mcp 1.x)")
    from archilens.mcp import cache, server

    repo = _repo(tmp_path / "repo", {"app/orders/a.py": "import psycopg2\n", "app/billing/b.py": "import stripe\n"})
    monkeypatch.setattr(cache, "_CACHE_DB", tmp_path / "cache" / "snapshots.db")
    for attribute in ("_snapshot", "_config", "_repo_path"):
        monkeypatch.setattr(server, attribute, None)
    server.init_server(repo)
    result = asyncio.run(server.create_mcp_server().call_tool("get_architecture_context", {"scope": "L0"}))
    text = (result[0] if isinstance(result, tuple) else result)[0].text
    assert "## External Systems (detected from imports)" in text
    assert "- PostgreSQL (database): used by orders; imported as psycopg2" in text
    assert "- Stripe (external_api): used by billing; imported as stripe" in text

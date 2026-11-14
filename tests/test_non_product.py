"""
Example apps, docs and benchmarks at the repository root are not architecture;
init also leaves out tooling and integration-test folders, visibly. Found by
running ArchiLens on Express, FastAPI, NestJS and Flask.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from click.testing import CliRunner

from archilens.analyzers.discovery import discover_files
from archilens.cli import _tolerant_streams, main
from archilens.config import ArchiLensConfig, load_config


def _repo(root: Path, files: list[str]) -> Path:
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n" if rel.endswith(".py") else "class A {}\n", encoding="utf-8")
    return root


def _paths(repo: Path) -> set[str]:
    files, _ = discover_files(repo, ArchiLensConfig())
    return {f.relative_path for f in files}


def test_root_examples_docs_and_benchmarks_are_excluded_by_default(tmp_path):
    repo = _repo(tmp_path, [
        "lib/router.py",
        "examples/mvc/app.py", "example/app.py", "samples/a.py", "sample/37-outbox/main.py",
        "demo/app.py", "demos/b.py", "docs/conf.py", "docs_src/tutorial.py",
        "benchmarks/run.py", "benchmark/run.py",
    ])
    assert _paths(repo) == {"lib/router.py"}


def test_same_names_deeper_down_are_product_code(tmp_path):
    # Spring Initializr's default package is com.example.demo; PetClinic's is ...samples.petclinic.
    repo = _repo(tmp_path, [
        "src/main/java/com/example/demo/DemoApplication.java",
        "src/main/java/org/springframework/samples/petclinic/owner/Owner.java",
        "packages/core/docs/render.py",
    ])
    assert _paths(repo) == {
        "src/main/java/com/example/demo/DemoApplication.java",
        "src/main/java/org/springframework/samples/petclinic/owner/Owner.java",
        "packages/core/docs/render.py",
    }


def test_init_leaves_out_tooling_and_integration_folders_and_says_so(tmp_path):
    _repo(tmp_path, [
        "packages/core/a.py", "packages/core/b.py", "packages/common/c.py",
        "integration/scopes/src/app.py", "scripts/release.py", "tools/gen.py",
    ])
    result = CliRunner().invoke(main, ["init", "--repo", str(tmp_path)])
    out = " ".join(result.output.split())
    assert "Found 3 Python files in 2 modules." in out
    assert "Left out integration/, scripts/, tools/" in out

    written = (tmp_path / ".archilens.yml").read_text(encoding="utf-8")
    assert '    - "integration/**"  # looks like integration tests; delete this line to analyse it' in written
    assert "#   - integration" not in written  # not offered as a module either
    config = load_config(tmp_path)
    # The written list starts from the defaults, so nothing they cover is lost.
    assert set(ArchiLensConfig().analysis.exclude) <= set(config.analysis.exclude)
    assert _paths_with(tmp_path, config) == {"packages/core/a.py", "packages/core/b.py", "packages/common/c.py"}


def test_init_keeps_folders_it_has_no_reason_to_doubt(tmp_path):
    _repo(tmp_path, ["app/a.py", "client/b.py"])
    result = CliRunner().invoke(main, ["init", "--repo", str(tmp_path)])
    assert "Left out" not in result.output


def _paths_with(repo: Path, config: ArchiLensConfig) -> set[str]:
    files, _ = discover_files(repo, config)
    return {f.relative_path for f in files}


def test_a_legacy_windows_console_shows_a_placeholder_instead_of_crashing(monkeypatch):
    raw = io.BytesIO()
    cp1252 = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stderr", cp1252)
    _tolerant_streams()
    cp1252.write("⠋ Discovering files")  # Rich's spinner frame
    cp1252.flush()
    assert raw.getvalue() == b"? Discovering files"

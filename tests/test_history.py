"""
`archilens history` on a real git repository: each ref goes through the same
analysis as `archilens analyze`, refs default to semver tags plus HEAD, and
module and dependency changes are tracked between refs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from archilens.analyzers.evolution import analyze_evolution, generate_evolution_report
from archilens.config import ArchiLensConfig

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.com", "-C", str(repo), *args],
                   check=True, capture_output=True)


def _commit(repo: Path, files: dict[str, str], message: str, tag: str | None = None) -> None:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    if tag:
        _git(repo, "tag", tag)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {
        "app/orders/service.py": "class OrderService:\n    pass\n",
        "app/payments/gateway.py": "def charge():\n    pass\n",
        "README.md": "docs\n",
    }, "v1", tag="v1.0")
    _commit(tmp_path, {
        "app/orders/service.py": "from app.payments.gateway import charge\n\nclass OrderService:\n    pass\n",
        "app/shipping/carrier.py": "class Carrier:\n    pass\n",
        "tests/test_orders.py": "import app.orders.service\n",
    }, "v2", tag="v2.0")
    _commit(tmp_path, {
        "app/shipping/carrier.py": "from app.payments.gateway import charge\n\nclass Carrier:\n    pass\n",
    }, "unreleased work")
    return tmp_path


def test_default_refs_are_tags_then_head(repo):
    timeline = analyze_evolution(repo, ArchiLensConfig())
    assert [s.ref for s in timeline.refs] == ["v1.0", "v2.0", "HEAD"]


def test_each_ref_is_really_analysed(repo):
    timeline = analyze_evolution(repo, ArchiLensConfig())
    v1, v2, head = timeline.refs
    assert sorted(v1.modules) == ["app/orders", "app/payments"]
    assert v1.dependencies == []
    assert sorted(v2.modules) == ["app/orders", "app/payments", "app/shipping"]  # tests/ is not a module
    assert v2.dependencies == [("app/orders", "app/payments")]
    # 3 modules + OrderService, Carrier + gateway.py, a file of functions
    assert v2.edge_count == 1 and v2.node_count == 3 + 2 + 1
    assert v2.file_count == 3
    assert head.dependencies == [("app/orders", "app/payments"), ("app/shipping", "app/payments")]


def test_module_and_dependency_changes_between_refs(repo):
    timeline = analyze_evolution(repo, ArchiLensConfig())
    assert ("app/shipping", "v2.0") in timeline.additions
    assert timeline.dependency_additions == [
        ("app/orders", "app/payments", "v2.0"),
        ("app/shipping", "app/payments", "HEAD"),
    ]
    report = generate_evolution_report(timeline)
    assert "| `HEAD` | added | app/shipping -> app/payments |" in report


def test_configured_globs_expand_to_tags_and_missing_refs_are_skipped(repo):
    config = ArchiLensConfig()
    config.baseline_refs = ["no-such-branch", "tags/v1*"]
    assert [s.ref for s in analyze_evolution(repo, config).refs] == ["v1.0", "HEAD"]


def test_explicit_refs_are_used_as_given(repo):
    timeline = analyze_evolution(repo, ArchiLensConfig(), refs=["v2.0", "v1.0"])
    assert [s.ref for s in timeline.refs] == ["v2.0", "v1.0"]


def test_head_not_repeated_when_it_is_the_last_tag(repo):
    _git(repo, "tag", "v3.0")
    assert [s.ref for s in analyze_evolution(repo, ArchiLensConfig()).refs] == ["v1.0", "v2.0", "v3.0"]


def test_working_tree_is_untouched(repo):
    before = (repo / "app/shipping/carrier.py").read_text(encoding="utf-8")
    analyze_evolution(repo, ArchiLensConfig())
    assert (repo / "app/shipping/carrier.py").read_text(encoding="utf-8") == before
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""

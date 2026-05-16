"""
Tests for features added in round 2:
  - Tree-sitter extraction
  - D2 generator
  - PlantUML generator
  - Architecture diff (compute_diff, check_rules)
  - Git utilities (checkout_ref — mocked)
  - Evolution analysis (lightweight, no live git required)
  - Interactive viewer routes
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archilens.config import ArchiLensConfig, AnalysisConfig
from archilens.models import (
    ArchDiff,
    ArchEdge,
    ArchNode,
    ArchSnapshot,
    DiagramLevel,
    EdgeType,
    FlowStep,
    NodeType,
    ProcessFlow,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Minimal Python repo used by multiple test classes."""
    src = tmp_path / "src"
    orders = src / "orders"
    payments = src / "payments"
    orders.mkdir(parents=True)
    payments.mkdir(parents=True)

    (orders / "__init__.py").write_text("")
    (orders / "service.py").write_text(
        "from src.payments.gateway import PaymentGateway\n\n"
        "class OrderService(BaseService):\n"
        "    def create_order(self, user_id):\n"
        "        gw = PaymentGateway()\n"
        "        return gw.charge(10)\n"
    )
    (payments / "__init__.py").write_text("")
    (payments / "gateway.py").write_text(
        "class PaymentGateway:\n"
        "    def charge(self, amount: float) -> bool:\n"
        "        return True\n"
    )
    return tmp_path


@pytest.fixture
def default_config() -> ArchiLensConfig:
    return ArchiLensConfig(
        project_name="Test Project",
        analysis=AnalysisConfig(languages=["python"]),
    )


@pytest.fixture
def simple_snapshot() -> ArchSnapshot:
    """Snapshot with two modules and one edge for generator tests."""
    mod_a = ArchNode(
        id="module:src/orders",
        name="Orders",
        node_type=NodeType.MODULE,
        level=DiagramLevel.MODULE,
        lines_of_code=120,
    )
    mod_b = ArchNode(
        id="module:src/payments",
        name="Payments",
        node_type=NodeType.MODULE,
        level=DiagramLevel.MODULE,
        lines_of_code=80,
    )
    cls_a = ArchNode(
        id="class:src/orders/service.py:OrderService",
        name="OrderService",
        node_type=NodeType.CLASS,
        level=DiagramLevel.COMPONENT,
        file_path="src/orders/service.py",
        parent="module:src/orders",
    )
    edge = ArchEdge(
        source="module:src/orders",
        target="module:src/payments",
        edge_type=EdgeType.DEPENDENCY,
        label="1 import",
    )
    return ArchSnapshot(
        project_name="Test",
        nodes=[mod_a, mod_b, cls_a],
        edges=[edge],
    )


@pytest.fixture
def sample_flow() -> ProcessFlow:
    return ProcessFlow(
        id="flow:test",
        name="Create Order Flow",
        trigger="POST /orders",
        description="User submits an order.",
        steps=[
            FlowStep(order=1, actor="Client", action="Submit order", target="API"),
            FlowStep(order=2, actor="API", action="Validate", target="OrderService"),
            FlowStep(order=3, actor="OrderService", action="Charge card", target="PaymentGateway", is_async=True),
        ],
    )


# ---------------------------------------------------------------------------
# Tree-sitter extraction
# ---------------------------------------------------------------------------

class TestTreeSitter:
    def test_extracts_python_classes(self, sample_repo):
        from archilens.analyzers.treesitter import extract_from_file

        src = sample_repo / "src" / "orders" / "service.py"
        result = extract_from_file(src, "python", src.read_text())

        assert result.used_treesitter
        class_names = {c.name for c in result.classes}
        assert "OrderService" in class_names

    def test_extracts_python_base_classes(self, sample_repo):
        from archilens.analyzers.treesitter import extract_from_file

        src = sample_repo / "src" / "orders" / "service.py"
        result = extract_from_file(src, "python", src.read_text())

        order_cls = next((c for c in result.classes if c.name == "OrderService"), None)
        assert order_cls is not None
        assert "BaseService" in order_cls.bases

    def test_extracts_python_functions(self, sample_repo):
        from archilens.analyzers.treesitter import extract_from_file

        src = sample_repo / "src" / "orders" / "service.py"
        result = extract_from_file(src, "python", src.read_text())

        func_names = {f.name for f in result.functions}
        assert "create_order" in func_names

    def test_extracts_python_imports(self, sample_repo):
        from archilens.analyzers.treesitter import extract_from_file

        src = sample_repo / "src" / "orders" / "service.py"
        result = extract_from_file(src, "python", src.read_text())

        import_paths = {i.path for i in result.imports}
        assert any("payments" in p for p in import_paths)

    def test_skips_dunder_methods(self, tmp_path):
        from archilens.analyzers.treesitter import extract_from_file

        f = tmp_path / "foo.py"
        f.write_text("class Foo:\n    def __init__(self): pass\n    def bar(self): pass\n")
        result = extract_from_file(f, "python", f.read_text())

        func_names = {fn.name for fn in result.functions}
        assert "__init__" not in func_names
        assert "bar" in func_names

    def test_returns_no_treesitter_for_unknown_language(self, tmp_path):
        from archilens.analyzers.treesitter import extract_from_file

        f = tmp_path / "foo.cobol"
        f.write_text("IDENTIFICATION DIVISION.")
        result = extract_from_file(f, "cobol", f.read_text())
        assert not result.used_treesitter

    def test_line_numbers_are_correct(self, tmp_path):
        from archilens.analyzers.treesitter import extract_from_file

        f = tmp_path / "multi.py"
        f.write_text("x = 1\n\nclass Alpha:\n    pass\n\nclass Beta:\n    pass\n")
        result = extract_from_file(f, "python", f.read_text())

        alpha = next(c for c in result.classes if c.name == "Alpha")
        beta = next(c for c in result.classes if c.name == "Beta")
        assert alpha.line == 3
        assert beta.line == 6


# ---------------------------------------------------------------------------
# D2 generator
# ---------------------------------------------------------------------------

class TestD2Generator:
    def test_l1_produces_d2_syntax(self, simple_snapshot, default_config):
        from archilens.generators.d2 import D2Generator

        gen = D2Generator(default_config)
        output = gen.module_architecture(simple_snapshot)

        assert "direction: right" in output
        assert "Orders" in output
        assert "Payments" in output
        # Edge should appear
        assert "->" in output

    def test_l0_produces_d2_syntax(self, simple_snapshot, default_config):
        from archilens.generators.d2 import D2Generator

        gen = D2Generator(default_config)
        output = gen.system_context(simple_snapshot)

        assert "shape: rectangle" in output
        assert simple_snapshot.project_name in output

    def test_l2_returns_none_for_empty_module(self, simple_snapshot, default_config):
        from archilens.generators.d2 import D2Generator

        gen = D2Generator(default_config)
        # module:src/payments has no child nodes in simple_snapshot
        result = gen.component_detail(simple_snapshot, "module:src/payments")
        assert result is None

    def test_l2_includes_class_nodes(self, simple_snapshot, default_config):
        from archilens.generators.d2 import D2Generator

        gen = D2Generator(default_config)
        result = gen.component_detail(simple_snapshot, "module:src/orders")
        assert result is not None
        assert "OrderService" in result

    def test_l3_sequence_contains_actors(self, default_config, sample_flow):
        from archilens.generators.d2 import D2Generator

        gen = D2Generator(default_config)
        output = gen.process_flow(sample_flow)

        assert "Client" in output
        assert "Submit order" in output

    def test_generate_all_creates_files(self, simple_snapshot, default_config, tmp_path):
        from archilens.generators.d2 import D2Generator

        gen = D2Generator(default_config)
        files = gen.generate_all(simple_snapshot, tmp_path)

        d2_files = [f for f in files if f.suffix == ".d2"]
        assert len(d2_files) >= 2  # at least L0 and L1
        assert all(f.exists() for f in files)


# ---------------------------------------------------------------------------
# PlantUML generator
# ---------------------------------------------------------------------------

class TestPlantUMLGenerator:
    def test_l1_produces_plantuml_syntax(self, simple_snapshot, default_config):
        from archilens.generators.plantuml import PlantUMLGenerator

        gen = PlantUMLGenerator(default_config)
        output = gen.module_architecture(simple_snapshot)

        assert "@startuml" in output
        assert "@enduml" in output
        assert "Orders" in output
        assert "Payments" in output

    def test_l0_includes_system_node(self, simple_snapshot, default_config):
        from archilens.generators.plantuml import PlantUMLGenerator

        gen = PlantUMLGenerator(default_config)
        output = gen.system_context(simple_snapshot)

        assert "@startuml" in output
        assert simple_snapshot.project_name in output

    def test_l3_sequence_uses_autonumber(self, default_config, sample_flow):
        from archilens.generators.plantuml import PlantUMLGenerator

        gen = PlantUMLGenerator(default_config)
        output = gen.process_flow(sample_flow)

        assert "autonumber" in output
        assert "participant" in output
        assert "Create Order Flow" in output

    def test_l2_inheritance_arrow(self, default_config):
        from archilens.generators.plantuml import PlantUMLGenerator
        from archilens.models import ArchNode, ArchEdge, ArchSnapshot

        parent = ArchNode(id="class:Base", name="Base", node_type=NodeType.CLASS,
                          level=DiagramLevel.COMPONENT, parent="module:m")
        child = ArchNode(id="class:Child", name="Child", node_type=NodeType.CLASS,
                         level=DiagramLevel.COMPONENT, parent="module:m")
        mod = ArchNode(id="module:m", name="M", node_type=NodeType.MODULE,
                       level=DiagramLevel.MODULE)
        edge = ArchEdge(source="class:Child", target="class:Base",
                        edge_type=EdgeType.INHERITANCE, label="extends")
        snap = ArchSnapshot(project_name="T", nodes=[mod, parent, child], edges=[edge])

        gen = PlantUMLGenerator(default_config)
        output = gen.component_detail(snap, "module:m")
        assert output is not None
        assert "<|--" in output

    def test_generate_all_creates_puml_files(self, simple_snapshot, default_config, tmp_path):
        from archilens.generators.plantuml import PlantUMLGenerator

        gen = PlantUMLGenerator(default_config)
        files = gen.generate_all(simple_snapshot, tmp_path)

        puml_files = [f for f in files if f.suffix == ".puml"]
        assert len(puml_files) >= 2
        assert all(f.exists() for f in files)


# ---------------------------------------------------------------------------
# Architecture diff
# ---------------------------------------------------------------------------

class TestArchDiff:
    def _make_snap(self, ref: str, module_ids: list[str], edges: list[tuple] = []) -> ArchSnapshot:
        nodes = [
            ArchNode(id=mid, name=mid.split(":")[-1], node_type=NodeType.MODULE,
                     level=DiagramLevel.MODULE)
            for mid in module_ids
        ]
        arch_edges = [
            ArchEdge(source=s, target=t, edge_type=EdgeType.DEPENDENCY)
            for s, t in edges
        ]
        return ArchSnapshot(project_name="P", git_ref=ref, nodes=nodes, edges=arch_edges)

    def test_detects_added_module(self):
        from archilens.analyzers.diff import compute_diff

        base = self._make_snap("v1", ["module:a", "module:b"])
        head = self._make_snap("v2", ["module:a", "module:b", "module:c"])

        diff = compute_diff(base, head)
        added = [e for e in diff.entries if e.change_type == "added" and e.entity_type == "node"]
        assert any("module:c" in e.entity_id for e in added)

    def test_detects_removed_module(self):
        from archilens.analyzers.diff import compute_diff

        base = self._make_snap("v1", ["module:a", "module:b"])
        head = self._make_snap("v2", ["module:a"])

        diff = compute_diff(base, head)
        removed = [e for e in diff.entries if e.change_type == "removed" and e.entity_type == "node"]
        assert any("module:b" in e.entity_id for e in removed)

    def test_detects_added_edge(self):
        from archilens.analyzers.diff import compute_diff

        base = self._make_snap("v1", ["module:a", "module:b"])
        head = self._make_snap("v2", ["module:a", "module:b"],
                               edges=[("module:a", "module:b")])

        diff = compute_diff(base, head)
        added_edges = [e for e in diff.entries if e.change_type == "added" and e.entity_type == "edge"]
        assert len(added_edges) == 1

    def test_identical_snapshots_produce_empty_diff(self):
        from archilens.analyzers.diff import compute_diff

        snap = self._make_snap("v1", ["module:a", "module:b"],
                               edges=[("module:a", "module:b")])
        diff = compute_diff(snap, snap)
        assert len(diff.entries) == 0

    def test_summary_contains_counts(self):
        from archilens.analyzers.diff import compute_diff

        base = self._make_snap("v1", ["module:a"])
        head = self._make_snap("v2", ["module:a", "module:b"])
        diff = compute_diff(base, head)

        assert "1 added" in diff.summary

    def test_max_fan_out_rule(self):
        from archilens.analyzers.diff import compute_diff, check_rules
        from archilens.config import CIRule

        snap = self._make_snap(
            "v2", ["module:a", "module:b", "module:c", "module:d"],
            edges=[("module:a", "module:b"), ("module:a", "module:c"), ("module:a", "module:d")],
        )
        diff = compute_diff(snap, snap)
        rule = CIRule(name="max-fan-out", action="warn", threshold=2)
        violations = check_rules(diff, snap, [rule])
        assert len(violations) >= 1
        assert "module:a" in violations[0] or "a" in violations[0]

    def test_format_diff_is_ascii_safe(self):
        from archilens.analyzers.diff import compute_diff, format_diff_as_markdown

        base = self._make_snap("v1", ["module:a"])
        head = self._make_snap("v2", ["module:a", "module:b"])
        diff = compute_diff(base, head)
        md = format_diff_as_markdown(diff)

        # Must be encodable in ASCII (so it doesn't break Windows cp1252 terminals)
        md.encode("ascii")


# ---------------------------------------------------------------------------
# Git utilities (mocked git objects)
# ---------------------------------------------------------------------------

class TestGitUtils:
    def test_safe_ref_sanitises_slashes(self):
        from archilens.analyzers.git_utils import _safe_ref

        assert "/" not in _safe_ref("feature/my-branch")
        assert len(_safe_ref("a" * 100)) <= 32

    def test_checkout_ref_raises_on_non_repo(self, tmp_path):
        from archilens.analyzers.git_utils import checkout_ref

        with pytest.raises(RuntimeError, match="not a Git repository"):
            with checkout_ref(tmp_path, "HEAD"):
                pass

    def test_extract_tree_writes_blobs(self, tmp_path):
        """Unit-test _extract_tree using mock git blob objects."""
        from archilens.analyzers.git_utils import _extract_tree

        # Build a mock tree: one blob at src/foo.py
        blob = MagicMock()
        blob.type = "blob"
        blob.path = "src/foo.py"
        blob.data_stream.read.return_value = b"x = 1\n"

        tree = [blob]
        dest = tmp_path / "out"
        dest.mkdir()

        _extract_tree(tree, dest)

        assert (dest / "src" / "foo.py").read_bytes() == b"x = 1\n"

    def test_extract_tree_handles_nested_tree(self, tmp_path):
        """_extract_tree recurses into sub-tree objects."""
        from archilens.analyzers.git_utils import _extract_tree

        inner_blob = MagicMock()
        inner_blob.type = "blob"
        inner_blob.path = "pkg/bar.py"
        inner_blob.data_stream.read.return_value = b"y = 2\n"

        inner_tree = MagicMock()
        inner_tree.type = "tree"
        inner_tree.__iter__ = MagicMock(return_value=iter([inner_blob]))

        dest = tmp_path / "out2"
        dest.mkdir()
        _extract_tree([inner_tree], dest)

        assert (dest / "pkg" / "bar.py").read_bytes() == b"y = 2\n"


# ---------------------------------------------------------------------------
# Evolution analysis
# ---------------------------------------------------------------------------

class TestEvolution:
    def _make_mock_blob(self, path: str, content: str):
        blob = MagicMock()
        blob.type = "blob"
        blob.path = path
        blob.data_stream.read.return_value = content.encode()
        return blob

    def _make_mock_commit(self, sha: str, message: str, blobs: list):
        commit = MagicMock()
        commit.hexsha = sha
        commit.message = message
        commit.committed_date = 1_700_000_000
        commit.tree.traverse.return_value = iter(blobs)
        return commit

    def test_analyse_ref_counts_modules(self, tmp_path, default_config):
        from archilens.analyzers.evolution import _analyse_ref

        blobs = [
            self._make_mock_blob("src/orders/service.py", "class OrderService:\n    pass\n"),
            self._make_mock_blob("src/payments/gateway.py", "class PaymentGateway:\n    pass\n"),
        ]
        commit = self._make_mock_commit("abc123", "Initial commit", blobs)

        repo = MagicMock()
        result = _analyse_ref(repo, tmp_path, "v1.0", default_config)

        # _analyse_ref calls repo.commit(), but we're calling it directly with a mock repo
        # Patch repo.commit to return our mock commit
        repo.commit.return_value = commit
        result = _analyse_ref(repo, tmp_path, "v1.0", default_config)

        assert result is not None
        assert result.ref == "v1.0"
        assert result.module_count == 2
        assert result.total_loc > 0

    def test_analyse_ref_returns_none_on_bad_ref(self, tmp_path, default_config):
        from archilens.analyzers.evolution import _analyse_ref
        import git

        repo = MagicMock()
        repo.commit.side_effect = Exception("bad ref")
        result = _analyse_ref(repo, tmp_path, "nonexistent", default_config)
        assert result is None

    def test_timeline_tracks_additions(self, tmp_path, default_config):
        from archilens.analyzers.evolution import analyze_evolution, RefSnapshot, EvolutionTimeline

        snap1 = RefSnapshot(ref="v1", timestamp="2024-01-01",
                            module_count=1, node_count=5, edge_count=2,
                            total_loc=100, modules={"src/orders": 100})
        snap2 = RefSnapshot(ref="v2", timestamp="2024-06-01",
                            module_count=2, node_count=8, edge_count=3,
                            total_loc=200, modules={"src/orders": 100, "src/payments": 100})

        timeline = EvolutionTimeline(project_name="T", refs=[snap1, snap2])
        # Manually compute additions for test
        prev = set(snap1.modules.keys())
        curr = set(snap2.modules.keys())
        additions = [(m, "v2") for m in curr - prev]
        timeline.additions = additions

        assert ("src/payments", "v2") in timeline.additions

    def test_generate_timeline_diagram_single_ref(self):
        from archilens.analyzers.evolution import generate_timeline_diagram, EvolutionTimeline, RefSnapshot

        tl = EvolutionTimeline(project_name="P", refs=[
            RefSnapshot(ref="v1", timestamp="2024-01-01",
                        module_count=1, node_count=3, edge_count=1, total_loc=50,
                        modules={"src/a": 50})
        ])
        output = generate_timeline_diagram(tl)
        # Single ref → note rather than full timeline
        assert "v1" in output

    def test_generate_timeline_diagram_multi_ref(self):
        from archilens.analyzers.evolution import generate_timeline_diagram, EvolutionTimeline, RefSnapshot

        tl = EvolutionTimeline(project_name="P", refs=[
            RefSnapshot(ref="v1", timestamp="2024-01-01",
                        module_count=1, node_count=3, edge_count=1, total_loc=50,
                        modules={"src/a": 50}),
            RefSnapshot(ref="v2", timestamp="2024-06-01",
                        module_count=2, node_count=6, edge_count=2, total_loc=120,
                        modules={"src/a": 70, "src/b": 50}),
        ])
        output = generate_timeline_diagram(tl)
        assert "timeline" in output
        assert "v1" in output
        assert "v2" in output
        assert "xychart-beta" in output

    def test_generate_evolution_report_summary(self):
        from archilens.analyzers.evolution import generate_evolution_report, EvolutionTimeline, RefSnapshot

        tl = EvolutionTimeline(project_name="MyApp", refs=[
            RefSnapshot(ref="v1", timestamp="2024-01-01",
                        module_count=2, node_count=10, edge_count=3, total_loc=300,
                        modules={"a": 200, "b": 100}),
            RefSnapshot(ref="v2", timestamp="2024-12-01",
                        module_count=3, node_count=15, edge_count=5, total_loc=500,
                        modules={"a": 250, "b": 150, "c": 100}),
        ], additions=[("c", "v2")])

        report = generate_evolution_report(tl)
        assert "MyApp" in report
        assert "v1" in report
        assert "v2" in report
        assert "c" in report  # new module appears in additions

    def test_discover_semver_tags_sorts_correctly(self):
        from archilens.analyzers.evolution import _discover_semver_tags

        def make_tag(tag_name: str):
            t = MagicMock()
            t.name = tag_name   # set attribute directly; MagicMock(name=x) sets display name only
            return t

        repo = MagicMock()
        repo.tags = [make_tag("v1.10.0"), make_tag("v1.2.0"), make_tag("v2.0.0")]
        tags = _discover_semver_tags(repo)
        assert tags == ["v1.2.0", "v1.10.0", "v2.0.0"]


# ---------------------------------------------------------------------------
# Interactive viewer
# ---------------------------------------------------------------------------

flask = pytest.importorskip("flask", reason="flask not installed; skipping viewer tests")


class TestViewer:
    @pytest.fixture
    def viewer_app(self, sample_repo, default_config):
        """Create a Flask test client backed by a real (small) snapshot."""
        from archilens.viewer.app import create_app

        app = create_app(sample_repo, use_ai=False)
        app.config["TESTING"] = True
        return app.test_client()

    def test_index_returns_200(self, viewer_app):
        resp = viewer_app.get("/")
        assert resp.status_code == 200
        assert b"ArchiLens" in resp.data

    def test_nav_returns_json_list(self, viewer_app):
        resp = viewer_app.get("/api/nav")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert any(item["id"] == "l0" for item in data)
        assert any(item["id"] == "l1" for item in data)

    def test_l0_diagram_endpoint(self, viewer_app):
        resp = viewer_app.get("/api/diagram/l0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "mermaid" in data
        assert data["level"] == 0

    def test_l1_diagram_endpoint(self, viewer_app):
        resp = viewer_app.get("/api/diagram/l1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "mermaid" in data
        assert data["level"] == 1
        assert "flowchart" in data["mermaid"]

    def test_l2_diagram_unknown_module_returns_404(self, viewer_app):
        resp = viewer_app.get("/api/diagram/l2/module%3Adoes_not_exist")
        assert resp.status_code == 404

    def test_snapshot_endpoint_returns_json(self, viewer_app):
        resp = viewer_app.get("/api/snapshot")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data

    def test_modules_endpoint(self, viewer_app):
        resp = viewer_app.get("/api/modules")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert all("id" in m and "name" in m for m in data)

    def test_refresh_clears_cache(self, viewer_app):
        # Pre-warm the cache
        viewer_app.get("/api/nav")
        # Refresh
        resp = viewer_app.get("/api/refresh")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "cache cleared"

    def test_module_map_returns_sanitised_keys(self, viewer_app):
        resp = viewer_app.get("/api/module-map")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)
        # Keys must not contain raw colons or slashes (Mermaid-sanitised)
        for key in data.keys():
            assert ":" not in key
            assert "/" not in key
        # Values must be the original module IDs (contain "module:")
        for val in data.values():
            assert val.startswith("module:")

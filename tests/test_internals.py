"""
Tests for internal modules with previously low coverage:
  - analyzers/evolution.py   (generate_timeline_diagram, report, helpers)
  - analyzers/diff.py        (compute_diff, check_rules, format_diff_as_markdown)
  - generators/mermaid.py    (L2 component detail, L3 flow, index, L1 capability)
  - engine.py                (_apply_capability_mappings, _build_directory_tree)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archilens.config import ArchiLensConfig, Capability, CIRule, ExternalSystem
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

def _module_node(id_: str, name: str) -> ArchNode:
    return ArchNode(
        id=id_, name=name,
        node_type=NodeType.MODULE,
        level=DiagramLevel.MODULE,
        lines_of_code=100,
    )


def _class_node(id_: str, name: str, parent: str) -> ArchNode:
    return ArchNode(
        id=id_, name=name,
        node_type=NodeType.CLASS,
        level=DiagramLevel.COMPONENT,
        parent=parent,
    )


def _iface_node(id_: str, name: str, parent: str) -> ArchNode:
    return ArchNode(
        id=id_, name=name,
        node_type=NodeType.INTERFACE,
        level=DiagramLevel.COMPONENT,
        parent=parent,
    )


@pytest.fixture
def two_module_snap() -> ArchSnapshot:
    orders = _module_node("module:orders", "Orders")
    payments = _module_node("module:payments", "Payments")
    edge = ArchEdge(
        source="module:orders", target="module:payments",
        edge_type=EdgeType.DEPENDENCY, label="3 imports", weight=3,
    )
    return ArchSnapshot(
        project_name="Shop", git_ref="HEAD",
        nodes=[orders, payments], edges=[edge],
    )


# ===========================================================================
# evolution.py
# ===========================================================================

class TestEvolutionTimeline:
    def _make_snap(self, ref: str, **kwargs) -> "RefSnapshot":
        from archilens.analyzers.evolution import RefSnapshot
        defaults = dict(
            module_count=3, node_count=10, edge_count=5,
            total_loc=1000, modules={"orders": 500, "payments": 300, "users": 200},
            patterns=["layered"],
            commit_sha="abc12345", commit_message="release commit",
        )
        defaults.update(kwargs)
        return RefSnapshot(ref=ref, timestamp="2024-01-01", **defaults)

    def test_empty_timeline_diagram(self):
        from archilens.analyzers.evolution import EvolutionTimeline, generate_timeline_diagram
        tl = EvolutionTimeline(project_name="Test")
        result = generate_timeline_diagram(tl)
        assert "No refs" in result

    def test_single_ref_timeline_diagram(self):
        from archilens.analyzers.evolution import EvolutionTimeline, generate_timeline_diagram
        tl = EvolutionTimeline(project_name="Test", refs=[self._make_snap("v1.0")])
        result = generate_timeline_diagram(tl)
        assert "Only one ref" in result
        assert "v1.0" in result

    def test_multi_ref_timeline_diagram(self):
        from archilens.analyzers.evolution import EvolutionTimeline, generate_timeline_diagram
        tl = EvolutionTimeline(
            project_name="MyApp",
            refs=[self._make_snap("v1.0"), self._make_snap("v2.0", total_loc=2000)],
            additions=[("newmod", "v2.0")],
            removals=[("oldmod", "v2.0")],
        )
        result = generate_timeline_diagram(tl)
        assert "timeline" in result
        assert "```mermaid" in result
        assert "xychart-beta" in result
        assert "Added" in result
        assert "Removed" in result

    def test_evolution_report_empty(self):
        from archilens.analyzers.evolution import EvolutionTimeline, generate_evolution_report
        tl = EvolutionTimeline(project_name="Test")
        result = generate_evolution_report(tl)
        assert "No refs" in result

    def test_evolution_report_with_refs(self):
        from archilens.analyzers.evolution import EvolutionTimeline, generate_evolution_report
        tl = EvolutionTimeline(
            project_name="MyApp",
            refs=[self._make_snap("v1.0"), self._make_snap("v2.0", total_loc=2000)],
            additions=[("newmod", "v2.0")],
            removals=[("oldmod", "v2.0")],
        )
        result = generate_evolution_report(tl)
        assert "v1.0" in result
        assert "v2.0" in result
        assert "New Modules" in result
        assert "Removed Modules" in result

    def test_detect_patterns_mvc(self):
        from archilens.analyzers.evolution import _detect_patterns_lightweight
        mods = {"app/controllers", "app/views", "app/models"}
        patterns = _detect_patterns_lightweight(mods)
        assert "mvc" in patterns

    def test_detect_patterns_event_driven(self):
        from archilens.analyzers.evolution import _detect_patterns_lightweight
        mods = {"app/events", "app/messages"}
        patterns = _detect_patterns_lightweight(mods)
        assert "event_driven" in patterns

    def test_detect_patterns_repository(self):
        from archilens.analyzers.evolution import _detect_patterns_lightweight
        mods = {"app/repository", "app/service"}
        patterns = _detect_patterns_lightweight(mods)
        assert "repository" in patterns

    def test_detect_patterns_layered(self):
        from archilens.analyzers.evolution import _detect_patterns_lightweight
        mods = {"app/service", "app/handler", "app/api"}
        patterns = _detect_patterns_lightweight(mods)
        assert "layered" in patterns

    def test_is_source_file(self):
        from archilens.analyzers.evolution import _is_source_file
        exts = {".py", ".js"}
        assert _is_source_file("src/app.py", exts, [])
        assert not _is_source_file("src/app.txt", exts, [])
        # pattern must match file or its name
        assert not _is_source_file("src/pkg.py", exts, ["*/pkg.py"])

    def test_metrics_table(self):
        from archilens.analyzers.evolution import EvolutionTimeline, _metrics_table
        tl = EvolutionTimeline(
            project_name="T",
            refs=[self._make_snap("v1.0"), self._make_snap("v2.0")],
        )
        result = "\n".join(_metrics_table(tl))
        assert "v1.0" in result
        assert "v2.0" in result


# ===========================================================================
# diff.py
# ===========================================================================

class TestDiffCompute:
    def _snap(self, nodes=None, edges=None, flows=None, ref="HEAD") -> ArchSnapshot:
        return ArchSnapshot(
            project_name="T", git_ref=ref,
            nodes=nodes or [], edges=edges or [], flows=flows or [],
        )

    def test_detects_added_node(self):
        from archilens.analyzers.diff import compute_diff
        base = self._snap(ref="base")
        head = self._snap(nodes=[_module_node("module:new", "New")], ref="head")
        diff = compute_diff(base, head)
        assert any(e.change_type == "added" and e.entity_type == "node" for e in diff.entries)

    def test_detects_removed_node(self):
        from archilens.analyzers.diff import compute_diff
        base = self._snap(nodes=[_module_node("module:old", "Old")], ref="base")
        head = self._snap(ref="head")
        diff = compute_diff(base, head)
        assert any(e.change_type == "removed" and e.entity_type == "node" for e in diff.entries)

    def test_detects_added_edge(self):
        from archilens.analyzers.diff import compute_diff
        edge = ArchEdge(source="module:a", target="module:b", edge_type=EdgeType.DEPENDENCY)
        base = self._snap(ref="base")
        head = self._snap(edges=[edge], ref="head")
        diff = compute_diff(base, head)
        assert any(e.change_type == "added" and e.entity_type == "edge" for e in diff.entries)

    def test_detects_removed_edge(self):
        from archilens.analyzers.diff import compute_diff
        edge = ArchEdge(source="module:a", target="module:b", edge_type=EdgeType.DEPENDENCY)
        base = self._snap(edges=[edge], ref="base")
        head = self._snap(ref="head")
        diff = compute_diff(base, head)
        assert any(e.change_type == "removed" and e.entity_type == "edge" for e in diff.entries)

    def test_detects_added_flow(self):
        from archilens.analyzers.diff import compute_diff
        flow = ProcessFlow(id="flow:checkout", name="Checkout", trigger="POST /checkout")
        base = self._snap(ref="base")
        head = self._snap(flows=[flow], ref="head")
        diff = compute_diff(base, head)
        assert any(e.entity_type == "flow" for e in diff.entries)

    def test_summary_text(self):
        from archilens.analyzers.diff import compute_diff
        base = self._snap(nodes=[_module_node("module:old", "Old")], ref="base")
        head = self._snap(nodes=[_module_node("module:new", "New")], ref="head")
        diff = compute_diff(base, head)
        assert "added" in diff.summary
        assert "removed" in diff.summary

    def test_check_rules_no_layer_skip(self):
        from archilens.analyzers.diff import check_rules, compute_diff
        rule = CIRule(name="no-layer-skip", action="fail", from_pattern="ui/*", to_pattern="db/*")
        config = ArchiLensConfig()
        config.ci.rules = [rule]

        edge = ArchEdge(source="module:ui/app", target="module:db/repo",
                        edge_type=EdgeType.DEPENDENCY)
        head = self._snap(
            nodes=[_module_node("module:ui/app", "UI"), _module_node("module:db/repo", "DB")],
            edges=[edge],
        )
        base = self._snap()
        diff = compute_diff(base, head)
        violations = check_rules(diff, head, config.ci.rules)
        assert any("no-layer-skip" in v for v in violations)

    def test_check_rules_max_fan_out(self):
        from archilens.analyzers.diff import check_rules, compute_diff
        rule = CIRule(name="max-fan-out", action="warn", threshold=2)
        edge1 = ArchEdge(source="module:a", target="module:b", edge_type=EdgeType.DEPENDENCY)
        edge2 = ArchEdge(source="module:a", target="module:c", edge_type=EdgeType.DEPENDENCY)
        edge3 = ArchEdge(source="module:a", target="module:d", edge_type=EdgeType.DEPENDENCY)
        head = self._snap(edges=[edge1, edge2, edge3])
        base = self._snap()
        diff = compute_diff(base, head)
        violations = check_rules(diff, head, [rule])
        assert any("max-fan-out" in v for v in violations)

    def test_format_diff_markdown(self):
        from archilens.analyzers.diff import compute_diff, format_diff_as_markdown
        base = self._snap(nodes=[_module_node("module:old", "Old")], ref="v1")
        head = self._snap(nodes=[_module_node("module:new", "New")], ref="v2")
        diff = compute_diff(base, head)
        md = format_diff_as_markdown(diff)
        assert "## ArchiLens" in md
        assert "v1" in md
        assert "v2" in md

    def test_format_diff_with_violations(self):
        from archilens.analyzers.diff import ArchDiff, format_diff_as_markdown
        diff = ArchDiff(base_ref="v1", head_ref="v2")
        diff.rule_violations = ["[FAIL] no-layer-skip: forbidden dep"]
        md = format_diff_as_markdown(diff)
        assert "Rule Violations" in md
        assert "no-layer-skip" in md


# ===========================================================================
# generators/mermaid.py
# ===========================================================================

class TestMermaidL2:
    def test_component_detail_with_class_and_interface(self):
        from archilens.generators.mermaid import generate_component_detail

        parent = _module_node("module:orders", "Orders")
        parent.children = ["class:orders:Service", "class:orders:Repo"]

        svc = _class_node("class:orders:Service", "Service", "module:orders")
        repo = _iface_node("class:orders:Repo", "Repo", "module:orders")

        inherit_edge = ArchEdge(
            source="class:orders:Service", target="class:orders:Repo",
            edge_type=EdgeType.INHERITANCE, label="extends",
        )
        snap = ArchSnapshot(
            project_name="T", git_ref="HEAD",
            nodes=[parent, svc, repo], edges=[inherit_edge],
        )
        result = generate_component_detail(snap, "module:orders")
        assert result is not None
        assert "classDiagram" in result
        assert "Service" in result
        assert "Repo" in result
        assert "<|--" in result

    def test_component_detail_with_implementation_edge(self):
        from archilens.generators.mermaid import generate_component_detail

        parent = _module_node("module:orders", "Orders")
        parent.children = ["class:orders:Service", "class:orders:IService"]

        svc = _class_node("class:orders:Service", "Service", "module:orders")
        iface = _iface_node("class:orders:IService", "IService", "module:orders")

        impl_edge = ArchEdge(
            source="class:orders:Service", target="class:orders:IService",
            edge_type=EdgeType.IMPLEMENTATION, label="implements",
        )
        snap = ArchSnapshot(
            project_name="T", git_ref="HEAD",
            nodes=[parent, svc, iface], edges=[impl_edge],
        )
        result = generate_component_detail(snap, "module:orders")
        assert result is not None
        assert "<|.." in result

    def test_component_detail_with_composition_edge(self):
        from archilens.generators.mermaid import generate_component_detail

        parent = _module_node("module:orders", "Orders")
        parent.children = ["class:orders:A", "class:orders:B"]

        a = _class_node("class:orders:A", "A", "module:orders")
        b = _class_node("class:orders:B", "B", "module:orders")
        comp_edge = ArchEdge(
            source="class:orders:A", target="class:orders:B",
            edge_type=EdgeType.COMPOSITION, label="contains",
        )
        snap = ArchSnapshot(
            project_name="T", git_ref="HEAD",
            nodes=[parent, a, b], edges=[comp_edge],
        )
        result = generate_component_detail(snap, "module:orders")
        assert result is not None
        assert "*--" in result

    def test_component_detail_with_file_path(self):
        from archilens.generators.mermaid import generate_component_detail

        parent = _module_node("module:orders", "Orders")
        parent.children = ["class:orders:Service"]

        svc = _class_node("class:orders:Service", "Service", "module:orders")
        svc.file_path = "orders/service.py"

        snap = ArchSnapshot(
            project_name="T", git_ref="HEAD",
            nodes=[parent, svc], edges=[],
        )
        result = generate_component_detail(snap, "module:orders")
        assert result is not None
        assert "orders/service.py" in result


class TestMermaidL1Capabilities:
    def test_l1_with_capability_grouping(self, two_module_snap):
        from archilens.generators.mermaid import generate_module_architecture

        two_module_snap.capability_map = {"Commerce": ["module:orders", "module:payments"]}
        for n in two_module_snap.nodes:
            n.capability = "Commerce"

        result = generate_module_architecture(two_module_snap)
        assert "subgraph" in result
        assert "Commerce" in result

    def test_l1_no_modules_returns_note(self):
        from archilens.generators.mermaid import generate_module_architecture

        snap = ArchSnapshot(project_name="Empty", git_ref="HEAD")
        result = generate_module_architecture(snap)
        assert "No modules detected" in result

    def test_l0_with_external_systems_and_flows(self, two_module_snap):
        from archilens.generators.mermaid import generate_system_context

        config = ArchiLensConfig()
        config.external_systems = [
            ExternalSystem(name="Stripe", type="external_api", description="Payments"),
            ExternalSystem(name="Redis", type="cache", description="Cache"),
            ExternalSystem(name="Postgres", type="database", description="DB"),
        ]

        # A user-facing flow triggers the USER actor
        two_module_snap.flows.append(
            ProcessFlow(id="f1", name="Checkout", trigger="POST /checkout")
        )

        result = generate_system_context(two_module_snap, config)
        assert "Stripe" in result
        assert "Redis" in result
        assert "Postgres" in result
        assert "USER" in result


class TestMermaidL3:
    def test_sequence_with_condition_and_async(self):
        from archilens.generators.mermaid import generate_process_flow

        flow = ProcessFlow(
            id="flow:checkout",
            name="Checkout",
            trigger="POST /checkout",
            description="Handles checkout",
            steps=[
                FlowStep(order=1, actor="User", action="submit", target="API",
                         condition="authenticated"),
                FlowStep(order=2, actor="API", action="charge", target="Payments",
                         is_async=True, data='{"amount": 100}'),
            ],
        )
        result = generate_process_flow(flow)
        assert "sequenceDiagram" in result
        assert "alt authenticated" in result
        assert "->>" in result  # async arrow
        assert "->>+" in result  # sync arrow
        assert "amount" in result

    def test_sequence_empty_steps(self):
        from archilens.generators.mermaid import generate_process_flow

        flow = ProcessFlow(id="f", name="Empty flow", trigger="")
        result = generate_process_flow(flow)
        assert "sequenceDiagram" in result


class TestMermaidIndex:
    def test_index_with_capability_map(self, two_module_snap):
        from archilens.generators.mermaid import MermaidGenerator

        two_module_snap.capability_map = {"Commerce": ["module:orders"]}
        config = ArchiLensConfig()
        gen = MermaidGenerator(config)
        fake_files = [Path("L0.md"), Path("L1.md"), Path("INDEX.md")]
        result = gen.index(two_module_snap, fake_files)
        assert "Commerce" in result
        assert "L0" in result


# ===========================================================================
# engine.py helpers
# ===========================================================================

class TestEngineHelpers:
    def test_apply_capability_mappings(self, two_module_snap):
        from archilens.engine import _apply_capability_mappings

        config = ArchiLensConfig()
        # _apply_capability_mappings resolves via node.file_path or node.id.replace("module:", "")
        config.capabilities = [
            Capability(name="Payments", modules=["payments"]),
        ]
        _apply_capability_mappings(two_module_snap, config)

        payments_node = next(n for n in two_module_snap.nodes if n.id == "module:payments")
        assert payments_node.capability == "Payments"
        assert "Payments" in two_module_snap.capability_map

    def test_apply_capability_mappings_no_match(self, two_module_snap):
        from archilens.engine import _apply_capability_mappings

        config = ArchiLensConfig()
        config.capabilities = [Capability(name="Infra", modules=["module:infra/*"])]
        _apply_capability_mappings(two_module_snap, config)
        assert "Infra" not in two_module_snap.capability_map

    def test_build_directory_tree(self, tmp_path):
        from archilens.engine import _build_directory_tree

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x=1")
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()

        tree = _build_directory_tree(tmp_path, max_depth=2)
        assert "src" in tree
        assert ".git" not in tree          # filtered
        assert "node_modules" not in tree  # filtered

    def test_build_directory_tree_depth_limit(self, tmp_path):
        from archilens.engine import _build_directory_tree

        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "file.py").write_text("x=1")

        tree = _build_directory_tree(tmp_path, max_depth=2)
        assert "a" in tree
        assert "b" in tree
        assert "file.py" not in tree  # too deep for max_depth=2

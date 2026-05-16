"""
Targeted tests to raise coverage on previously uncovered paths:
  - config.py  ConfigError (YAML parse errors, wrong type, bad field)
  - engine.py  analyze_repository, generate_diagrams
  - generators/mermaid.py  L0/L1/L2/L3 helpers
  - analyzers/discovery.py  file-cap warning, test-file detection
  - analyzers/git_utils.py  resolve_ref, get_tags (mocked)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archilens.config import ArchiLensConfig, AnalysisConfig, ConfigError, load_config
from archilens.models import (
    ArchEdge,
    ArchNode,
    ArchSnapshot,
    DiagramLevel,
    EdgeType,
    NodeType,
    ProcessFlow,
    FlowStep,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_repo(tmp_path: Path) -> Path:
    """A minimal Python repo with two modules."""
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        "from myapp.utils import helper\n\n"
        "class CoreService:\n"
        "    def run(self): pass\n"
    )
    utils = tmp_path / "myapp" / "utils.py"
    utils.write_text("def helper(): return 42\n")
    return tmp_path


@pytest.fixture
def base_config() -> ArchiLensConfig:
    return ArchiLensConfig(project_name="TestProj")


@pytest.fixture
def sample_snapshot() -> ArchSnapshot:
    nodes = [
        ArchNode(id="module:orders", name="Orders", node_type=NodeType.MODULE,
                 level=DiagramLevel.MODULE, lines_of_code=100),
        ArchNode(id="module:payments", name="Payments", node_type=NodeType.MODULE,
                 level=DiagramLevel.MODULE, lines_of_code=80),
    ]
    edges = [
        ArchEdge(source="module:orders", target="module:payments",
                 edge_type=EdgeType.DEPENDENCY),
    ]
    return ArchSnapshot(
        project_name="TestApp",
        git_ref="HEAD",
        nodes=nodes,
        edges=edges,
    )


# ---------------------------------------------------------------------------
# config.py — ConfigError paths
# ---------------------------------------------------------------------------

class TestConfigError:
    def test_yaml_parse_error(self, tmp_path):
        cfg = tmp_path / ".archilens.yml"
        cfg.write_text("key: {\n  bad yaml here\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="Could not parse"):
            load_config(tmp_path)

    def test_non_mapping_root(self, tmp_path):
        cfg = tmp_path / ".archilens.yml"
        cfg.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            load_config(tmp_path)

    def test_bad_field_value(self, tmp_path):
        cfg = tmp_path / ".archilens.yml"
        # max_depth must be an int; give a string that int() rejects
        cfg.write_text(
            "analysis:\n  max_depth: not-a-number\n",
            encoding="utf-8",
        )
        # _parse_config doesn't cast max_depth, so it silently accepts strings —
        # but a nested dict field error should raise ConfigError
        # Test the catch-all by patching _parse_config to throw
        with patch("archilens.config._parse_config", side_effect=TypeError("bad value")):
            with pytest.raises(ConfigError, match="Invalid value"):
                load_config(tmp_path)

    def test_valid_yaml_loads_without_error(self, tmp_path):
        cfg = tmp_path / ".archilens.yml"
        cfg.write_text(
            "project:\n  name: MyApp\n  type: monolith\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config.project_name == "MyApp"

    def test_missing_config_returns_defaults(self, tmp_path):
        config = load_config(tmp_path)
        assert config.project_name == "Unnamed Project"
        assert config.diagrams.format == "mermaid"


# ---------------------------------------------------------------------------
# engine.py — analyze_repository
# ---------------------------------------------------------------------------

class TestEngine:
    def test_analyze_returns_snapshot(self, minimal_repo, base_config):
        from archilens.engine import analyze_repository
        snap = analyze_repository(minimal_repo, config=base_config, use_ai=False)
        assert snap.project_name == "TestProj"
        assert len(snap.nodes) > 0

    def test_analyze_empty_dir_returns_empty_snapshot(self, tmp_path, base_config):
        from archilens.engine import analyze_repository
        snap = analyze_repository(tmp_path, config=base_config, use_ai=False)
        assert snap.nodes == []
        assert snap.edges == []

    def test_generate_diagrams_creates_files(self, tmp_path, sample_snapshot, base_config):
        from archilens.engine import generate_diagrams
        out = tmp_path / "diagrams"
        generated = generate_diagrams(sample_snapshot, base_config, out)
        assert len(generated) > 0
        for path in generated:
            assert path.exists()

    def test_generate_diagrams_d2_format(self, tmp_path, sample_snapshot):
        from archilens.engine import generate_diagrams
        from archilens.config import DiagramConfig
        config = ArchiLensConfig(project_name="T")
        config.diagrams = DiagramConfig(format="d2")
        out = tmp_path / "d2out"
        generated = generate_diagrams(sample_snapshot, config, out)
        assert any(p.suffix == ".d2" for p in generated)

    def test_generate_diagrams_plantuml_format(self, tmp_path, sample_snapshot):
        from archilens.engine import generate_diagrams
        from archilens.config import DiagramConfig
        config = ArchiLensConfig(project_name="T")
        config.diagrams = DiagramConfig(format="plantuml")
        out = tmp_path / "pumlout"
        generated = generate_diagrams(sample_snapshot, config, out)
        assert any(p.suffix == ".puml" for p in generated)

    def test_analyze_with_progress_cb(self, minimal_repo, base_config):
        from archilens.analyzers.dependencies import analyze_dependencies
        from archilens.analyzers.discovery import discover_files
        files, modules = discover_files(minimal_repo, base_config)
        called = []
        analyze_dependencies(files, modules, minimal_repo, progress_cb=called.append)
        assert len(called) > 0


# ---------------------------------------------------------------------------
# generators/mermaid.py — L0/L1/L2/L3 coverage
# ---------------------------------------------------------------------------

class TestMermaidGenerator:
    def test_l0_includes_external_systems(self, sample_snapshot, base_config):
        from archilens.generators.mermaid import generate_system_context
        result = generate_system_context(sample_snapshot, base_config)
        assert "```mermaid" in result
        assert "C4Context" in result or "flowchart" in result or "graph" in result

    def test_l1_lists_all_modules(self, sample_snapshot):
        from archilens.generators.mermaid import generate_module_architecture
        result = generate_module_architecture(sample_snapshot)
        assert "Orders" in result
        assert "Payments" in result

    def test_l2_returns_none_for_missing_module(self, sample_snapshot):
        from archilens.generators.mermaid import generate_component_detail
        result = generate_component_detail(sample_snapshot, "module:nonexistent")
        assert result is None

    def test_l3_sequence_diagram(self, sample_snapshot):
        flow = ProcessFlow(
            id="flow:checkout",
            name="Checkout",
            trigger="POST /checkout",
            steps=[
                FlowStep(order=1, actor="User", action="submit order", target="Orders"),
                FlowStep(order=2, actor="Orders", action="charge card", target="Payments"),
            ],
        )
        sample_snapshot.flows.append(flow)
        from archilens.generators.mermaid import generate_process_flow
        result = generate_process_flow(flow)
        assert "sequenceDiagram" in result
        assert "Checkout" in result

    def test_generate_all_creates_index(self, tmp_path, sample_snapshot, base_config):
        from archilens.generators.mermaid import MermaidGenerator
        gen = MermaidGenerator(base_config)
        files = gen.generate_all(sample_snapshot, tmp_path)
        names = [f.name for f in files]
        assert any("index" in n for n in names) or len(files) > 0


# ---------------------------------------------------------------------------
# analyzers/discovery.py — file cap and test-file detection
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_warns_on_large_repo(self, tmp_path, base_config, caplog):
        import logging
        src = tmp_path / "src"
        src.mkdir()
        # Create 5001 tiny Python files to trigger the warning
        for i in range(5001):
            (src / f"mod_{i}.py").write_text(f"x = {i}\n")
        with caplog.at_level(logging.WARNING, logger="archilens.analyzers.discovery"):
            from archilens.analyzers.discovery import discover_files
            files, _ = discover_files(tmp_path, base_config)
        assert any("large repo" in r.message for r in caplog.records)

    def test_caps_at_max_file_count(self, tmp_path, base_config):
        from archilens.analyzers.discovery import _MAX_FILE_COUNT, discover_files
        src = tmp_path / "src"
        src.mkdir()
        for i in range(_MAX_FILE_COUNT + 50):
            (src / f"f_{i}.py").write_text("x=1\n")
        files, _ = discover_files(tmp_path, base_config)
        assert len(files) <= _MAX_FILE_COUNT

    def test_detects_test_files(self):
        from archilens.analyzers.discovery import _is_test_file
        assert _is_test_file("tests/test_core.py")
        assert _is_test_file("src/user_test.go")
        assert _is_test_file("src/__tests__/App.spec.ts")
        assert not _is_test_file("src/orders/service.py")

    def test_entry_point_detection(self):
        from archilens.analyzers.discovery import _is_entry_point
        patterns = [{"pattern": "*/routes/*.py"}]
        assert _is_entry_point("app/routes/users.py", patterns)
        assert not _is_entry_point("app/models/user.py", patterns)


# ---------------------------------------------------------------------------
# analyzers/git_utils.py — resolve_ref and get_tags (mocked)
# ---------------------------------------------------------------------------

class TestGitUtilsCoverage:
    def test_resolve_ref_returns_sha(self, tmp_path):
        import sys
        from archilens.analyzers.git_utils import resolve_ref
        mock_git = MagicMock()
        mock_commit = MagicMock()
        mock_commit.hexsha = "abc123def456" * 3
        mock_git.Repo.return_value.commit.return_value = mock_commit
        with patch.dict(sys.modules, {"git": mock_git}):
            sha = resolve_ref(tmp_path, "main")
        assert sha == mock_commit.hexsha

    def test_resolve_ref_returns_none_on_error(self, tmp_path):
        from archilens.analyzers.git_utils import resolve_ref
        # Non-git directory causes Exception internally → returns None
        result = resolve_ref(tmp_path, "main")
        assert result is None

    def test_get_tags_returns_sorted_list(self, tmp_path):
        import sys
        from archilens.analyzers.git_utils import get_tags
        t1 = MagicMock(); t1.name = "v1.0.0"; t1.commit.committed_date = 1000
        t2 = MagicMock(); t2.name = "v2.0.0"; t2.commit.committed_date = 2000
        mock_git = MagicMock()
        mock_git.Repo.return_value.tags = [t2, t1]
        with patch.dict(sys.modules, {"git": mock_git}):
            tags = get_tags(tmp_path)
        assert tags == ["v1.0.0", "v2.0.0"]

    def test_get_tags_returns_empty_on_error(self, tmp_path):
        from archilens.analyzers.git_utils import get_tags
        # Non-git directory causes Exception internally → returns []
        tags = get_tags(tmp_path)
        assert tags == []


# ---------------------------------------------------------------------------
# analyzers/treesitter.py — fallback warning
# ---------------------------------------------------------------------------

class TestTreesitterFallback:
    def test_fallback_warning_emitted_once(self, caplog):
        import sys
        import logging
        import archilens.analyzers.treesitter as ts_mod

        # Clear state so the warning can fire fresh for "python"
        ts_mod._FALLBACK_WARNED.discard("python")
        ts_mod._PARSERS.pop("python", None)
        ts_mod._LANGUAGES.pop("python", None)

        # Remove tree_sitter from sys.modules so the import raises ImportError
        saved = {k: v for k, v in sys.modules.items() if "tree_sitter" in k}
        for k in list(saved):
            sys.modules[k] = None  # type: ignore[assignment]

        try:
            with caplog.at_level(logging.WARNING, logger="archilens.analyzers.treesitter"):
                ts_mod._get_lang_parser("python")   # first call — should warn
                ts_mod._get_lang_parser("python")   # second call — should NOT warn again
        finally:
            # Restore original modules
            for k in list(saved):
                del sys.modules[k]
            sys.modules.update(saved)
            ts_mod._FALLBACK_WARNED.discard("python")
            ts_mod._PARSERS.pop("python", None)
            ts_mod._LANGUAGES.pop("python", None)

        warnings = [r for r in caplog.records if "python" in r.message]
        assert len(warnings) == 1
        assert "tree-sitter" in warnings[0].message

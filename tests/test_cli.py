"""Tests for the Click CLI (archilens/cli.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from archilens.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def minimal_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "service.py").write_text("class OrderService: pass\n")
    return tmp_path


def _empty_snap():
    from archilens.models import ArchSnapshot
    return ArchSnapshot(project_name="T", git_ref="HEAD")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_config(self, runner, tmp_path):
        result = runner.invoke(main, ["init", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / ".archilens.yml").exists()

    def test_skips_existing_config(self, runner, tmp_path):
        cfg = tmp_path / ".archilens.yml"
        cfg.write_text("original", encoding="utf-8")
        result = runner.invoke(main, ["init", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        assert cfg.read_text(encoding="utf-8") == "original"

    def test_copies_example_config_when_present(self, runner, tmp_path):
        example = tmp_path / "example_src"
        example.mkdir()
        example_cfg = Path(__file__).parent.parent / ".archilens.yml"
        if not example_cfg.exists():
            pytest.skip("No example .archilens.yml in repo root")
        result = runner.invoke(main, ["init", "--repo", str(tmp_path)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

class TestAnalyze:
    def test_nonexistent_repo_exits_1(self, runner):
        result = runner.invoke(main, ["analyze", "--repo", "/no/such/path/xyz", "--no-ai"])
        assert result.exit_code == 1

    def test_bad_yaml_config_exits_1(self, runner, tmp_path):
        (tmp_path / ".archilens.yml").write_text("- bad\n- yaml\n", encoding="utf-8")
        result = runner.invoke(main, ["analyze", "--repo", str(tmp_path), "--no-ai"])
        assert result.exit_code == 1

    def test_analyze_no_ai_exits_0(self, runner, minimal_repo):
        result = runner.invoke(main, ["analyze", "--repo", str(minimal_repo), "--no-ai"])
        assert result.exit_code == 0

    def test_analyze_json_output(self, runner, minimal_repo):
        result = runner.invoke(main, ["analyze", "--repo", str(minimal_repo), "--no-ai", "--json-output"])
        assert result.exit_code == 0

    def test_analyze_with_output_dir(self, runner, tmp_path):
        snap = _empty_snap()
        with patch("archilens.engine.analyze_repository", return_value=snap), \
             patch("archilens.engine.generate_diagrams", return_value=[]):
            result = runner.invoke(
                main,
                ["analyze", "--repo", str(tmp_path), "--no-ai", "--output", str(tmp_path / "out")],
            )
        assert result.exit_code == 0

    def test_analyze_format_d2(self, runner, tmp_path):
        snap = _empty_snap()
        with patch("archilens.engine.analyze_repository", return_value=snap), \
             patch("archilens.engine.generate_diagrams", return_value=[]):
            result = runner.invoke(
                main,
                ["analyze", "--repo", str(tmp_path), "--no-ai", "--format", "d2"],
            )
        assert result.exit_code == 0

    def test_analyze_level_filter(self, runner, tmp_path):
        snap = _empty_snap()
        with patch("archilens.engine.analyze_repository", return_value=snap), \
             patch("archilens.engine.generate_diagrams", return_value=[]):
            result = runner.invoke(
                main,
                ["analyze", "--repo", str(tmp_path), "--no-ai", "--level", "1"],
            )
        assert result.exit_code == 0

    def test_analyze_lists_generated_files(self, runner, tmp_path):
        snap = _empty_snap()
        fake_file = tmp_path / "L1.md"
        fake_file.write_text("mermaid", encoding="utf-8")
        with patch("archilens.engine.analyze_repository", return_value=snap), \
             patch("archilens.engine.generate_diagrams", return_value=[fake_file]):
            result = runner.invoke(main, ["analyze", "--repo", str(tmp_path), "--no-ai"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

class TestDiff:
    def test_nonexistent_repo_exits_1(self, runner):
        result = runner.invoke(
            main, ["diff", "--repo", "/no/such/path", "--base", "v1", "--head", "HEAD"]
        )
        assert result.exit_code == 1

    def test_bad_config_exits_1(self, runner, tmp_path):
        (tmp_path / ".archilens.yml").write_text("- bad\n", encoding="utf-8")
        result = runner.invoke(
            main, ["diff", "--repo", str(tmp_path), "--base", "v1", "--head", "HEAD"]
        )
        assert result.exit_code == 1

    def test_checkout_error_exits_1(self, runner, tmp_path):
        with patch("archilens.analyzers.git_utils.resolve_ref", return_value=None), \
             patch("archilens.analyzers.git_utils.checkout_ref",
                   side_effect=RuntimeError("not a git repo")):
            result = runner.invoke(
                main, ["diff", "--repo", str(tmp_path), "--base", "v1", "--head", "HEAD"]
            )
        assert result.exit_code == 1

    def test_diff_with_violations_exits_1(self, runner, tmp_path):
        from archilens.models import ArchDiff, ArchSnapshot
        base = ArchSnapshot(project_name="T", git_ref="base")
        head = ArchSnapshot(project_name="T", git_ref="head")
        diff_result = ArchDiff(base_ref="base", head_ref="head")
        diff_result.rule_violations = ["[FAIL] no-layer-skip"]

        with patch("archilens.analyzers.git_utils.resolve_ref", return_value="abc1234def5"),\
             patch("archilens.analyzers.git_utils.checkout_ref") as mock_ctx, \
             patch("archilens.engine.analyze_repository", side_effect=[base, head]), \
             patch("archilens.analyzers.diff.compute_diff", return_value=diff_result), \
             patch("archilens.analyzers.diff.check_rules", return_value=diff_result.rule_violations), \
             patch("archilens.analyzers.diff.format_diff_as_markdown", return_value="## diff"):
            mock_ctx.return_value.__enter__ = lambda s: tmp_path
            mock_ctx.return_value.__exit__ = lambda s, *a: False
            result = runner.invoke(
                main, ["diff", "--repo", str(tmp_path), "--base", "v1", "--head", "HEAD"]
            )
        assert result.exit_code == 1

    def test_diff_writes_output_file(self, runner, tmp_path):
        from archilens.models import ArchDiff, ArchSnapshot
        base = ArchSnapshot(project_name="T", git_ref="base")
        head = ArchSnapshot(project_name="T", git_ref="head")
        diff_result = ArchDiff(base_ref="base", head_ref="head")
        out_file = tmp_path / "diff.md"

        with patch("archilens.analyzers.git_utils.resolve_ref", return_value="abc1234def5"),\
             patch("archilens.analyzers.git_utils.checkout_ref") as mock_ctx, \
             patch("archilens.engine.analyze_repository", side_effect=[base, head]), \
             patch("archilens.analyzers.diff.compute_diff", return_value=diff_result), \
             patch("archilens.analyzers.diff.check_rules", return_value=[]), \
             patch("archilens.analyzers.diff.format_diff_as_markdown", return_value="## diff"):
            mock_ctx.return_value.__enter__ = lambda s: tmp_path
            mock_ctx.return_value.__exit__ = lambda s, *a: False
            result = runner.invoke(
                main,
                ["diff", "--repo", str(tmp_path), "--base", "v1", "--head", "HEAD",
                 "--output", str(out_file)],
            )
        assert result.exit_code == 0
        assert out_file.exists()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_nonexistent_repo_exits_1(self, runner):
        result = runner.invoke(main, ["history", "--repo", "/no/such/path"])
        assert result.exit_code == 1

    def test_bad_config_exits_1(self, runner, tmp_path):
        (tmp_path / ".archilens.yml").write_text("- bad\n", encoding="utf-8")
        result = runner.invoke(main, ["history", "--repo", str(tmp_path)])
        assert result.exit_code == 1

    def test_empty_timeline_exits_1(self, runner, tmp_path):
        from archilens.analyzers.evolution import EvolutionTimeline
        with patch("archilens.analyzers.evolution.analyze_evolution",
                   return_value=EvolutionTimeline(project_name="T")):
            result = runner.invoke(main, ["history", "--repo", str(tmp_path)])
        assert result.exit_code == 1

    def test_history_happy_path(self, runner, tmp_path):
        from archilens.analyzers.evolution import EvolutionTimeline, RefSnapshot
        snap = RefSnapshot(
            ref="v1.0", timestamp="2024-01-01", module_count=3,
            node_count=10, edge_count=4, total_loc=800,
        )
        timeline = EvolutionTimeline(
            project_name="MyApp",
            refs=[snap, snap],
            additions=[("newmod", "v1.0")],
            removals=[("oldmod", "v0.9")],
        )
        with patch("archilens.analyzers.evolution.analyze_evolution", return_value=timeline), \
             patch("archilens.analyzers.evolution.generate_timeline_diagram", return_value="md"), \
             patch("archilens.analyzers.evolution.generate_evolution_report", return_value="md"):
            result = runner.invoke(
                main,
                ["history", "--repo", str(tmp_path), "--refs", "v1.0,v2.0",
                 "--output", str(tmp_path / "evo")],
            )
        assert result.exit_code == 0

    def test_history_use_tags_flag(self, runner, tmp_path):
        from archilens.analyzers.evolution import EvolutionTimeline, RefSnapshot
        snap = RefSnapshot(
            ref="v1.0", timestamp="2024-01-01", module_count=1,
            node_count=2, edge_count=1, total_loc=100,
        )
        timeline = EvolutionTimeline(project_name="T", refs=[snap])
        with patch("archilens.analyzers.evolution.analyze_evolution", return_value=timeline), \
             patch("archilens.analyzers.evolution.generate_timeline_diagram", return_value=""), \
             patch("archilens.analyzers.evolution.generate_evolution_report", return_value=""):
            result = runner.invoke(
                main,
                ["history", "--repo", str(tmp_path), "--tags",
                 "--output", str(tmp_path / "evo")],
            )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

class TestServe:
    def test_nonexistent_repo_exits_1(self, runner):
        result = runner.invoke(main, ["serve", "--repo", "/no/such/path/xyz"])
        assert result.exit_code == 1

    def test_serve_starts_app(self, runner, tmp_path):
        mock_app = MagicMock()
        mock_app.run.return_value = None
        with patch("archilens.viewer.app.create_app", return_value=mock_app):
            result = runner.invoke(main, ["serve", "--repo", str(tmp_path)])
        assert result.exit_code == 0
        mock_app.run.assert_called_once()

    def test_serve_ai_flag(self, runner, tmp_path):
        mock_app = MagicMock()
        mock_app.run.return_value = None
        with patch("archilens.viewer.app.create_app", return_value=mock_app) as m:
            runner.invoke(main, ["serve", "--repo", str(tmp_path), "--ai"])
        m.assert_called_once_with(tmp_path.resolve(), use_ai=True)

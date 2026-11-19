"""
``analysis.exclude_also`` leaves out more without losing the defaults.

``analysis.exclude`` replaces the default list. A contributor's NestJS config set it to one
pattern of its own and so analysed 86 modules (sample and integration-test apps) where its
header described 10, while ``archilens check`` passed. ``exclude_also`` adds to the defaults.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from archilens.analyzers.discovery import discover_files
from archilens.cli import main
from archilens.config import ArchiLensConfig, _parse_config, load_config

ROOT = Path(__file__).parent.parent
DEFAULTS = ArchiLensConfig().analysis.exclude

FILES = {
    "src/app/service.py": "class Service:\n    pass\n",
    "sample/demo/main.py": "x = 1\n",  # left out by default
    "integration/cors/app.py": "x = 1\n",  # not a default
    "legacy/old/thing.py": "x = 1\n",  # not a default
}


def _repo(root: Path, config: str = "") -> Path:
    for rel, body in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    if config:
        (root / ".archilens.yml").write_text(config, encoding="utf-8")
    return root


def _analysed_folders(repo: Path) -> set[str]:
    files, _ = discover_files(repo, load_config(repo))
    return {f.relative_path.split("/")[0] for f in files}


def test_defaults_have_nothing_extra():
    analysis = ArchiLensConfig().analysis
    assert analysis.exclude_also == []
    assert analysis.excluded() == DEFAULTS


def test_no_config_leaves_out_only_the_defaults(tmp_path):
    assert _analysed_folders(_repo(tmp_path)) == {"src", "integration", "legacy"}


def test_exclude_also_adds_to_the_defaults(tmp_path):
    repo = _repo(tmp_path, 'analysis:\n  exclude_also:\n    - "integration/**"\n    - "legacy/**"\n')
    analysis = load_config(repo).analysis
    assert analysis.exclude == DEFAULTS  # untouched
    assert analysis.excluded() == [*DEFAULTS, "integration/**", "legacy/**"]
    assert _analysed_folders(repo) == {"src"}  # sample/ is still left out, by the defaults


def test_exclude_alone_still_replaces_them(tmp_path):
    # The documented behaviour, and the trap: one pattern of your own brings sample/ back.
    repo = _repo(tmp_path, 'analysis:\n  exclude:\n    - "legacy/**"\n')
    assert load_config(repo).analysis.exclude == ["legacy/**"]
    assert _analysed_folders(repo) == {"src", "sample", "integration"}


def test_both_keys_combine(tmp_path):
    repo = _repo(tmp_path, 'analysis:\n  exclude:\n    - "legacy/**"\n  exclude_also:\n    - "integration/**"\n')
    assert load_config(repo).analysis.excluded() == ["legacy/**", "integration/**"]
    assert _analysed_folders(repo) == {"src", "sample"}


def test_a_bare_string_is_one_pattern_and_an_empty_key_is_none(tmp_path):
    repo = _repo(tmp_path, 'analysis:\n  exclude_also: "legacy/**"\n')
    assert load_config(repo).analysis.exclude_also == ["legacy/**"]  # not ["l", "e", "g", ...]
    assert _analysed_folders(repo) == {"src", "integration"}

    (repo / ".archilens.yml").write_text("analysis:\n  exclude_also:\n  exclude:\n", encoding="utf-8")
    analysis = load_config(repo).analysis
    assert analysis.exclude_also == [] and analysis.exclude == DEFAULTS


def test_init_writes_the_full_list_and_says_why(tmp_path):
    repo = _repo(tmp_path)
    result = CliRunner().invoke(main, ["init", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    text = (repo / ".archilens.yml").read_text(encoding="utf-8")
    assert "`exclude` replaces ArchiLens's default list" in text
    assert "  # exclude_also:\n" in text  # offered, commented out
    analysis = load_config(repo).analysis
    assert set(DEFAULTS) <= set(analysis.exclude) and analysis.exclude_also == []
    assert "integration/**" in analysis.exclude  # init's own addition, in the full list


def test_the_nestjs_example_adds_to_the_defaults():
    raw = yaml.safe_load((ROOT / "docs" / "configs" / "nestjs.yml").read_text(encoding="utf-8"))
    assert "exclude" not in raw["analysis"]
    assert "integration/**" in raw["analysis"]["exclude_also"]
    analysis = _parse_config(raw).analysis
    assert analysis.exclude == DEFAULTS  # so sample/ and node_modules/ stay out
    assert analysis.excluded()[len(DEFAULTS) :] == raw["analysis"]["exclude_also"]


def test_the_readme_example_does_not_demonstrate_the_trap():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = readme.split("### Analysis", 1)[1].split("```yaml", 1)[1].split("```", 1)[0]
    assert "exclude_also:" in block and "\n  exclude:" not in block

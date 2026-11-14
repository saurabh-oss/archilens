"""
Configuration loader for ArchiLens.

Reads and validates the .archilens.yml configuration file from
the repository root, providing sensible defaults for any
omitted settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AnalysisConfig:
    languages: list[str] = field(default_factory=lambda: [])
    entry_points: list[dict[str, str]] = field(default_factory=list)
    exclude: list[str] = field(
        default_factory=lambda: [
            "**/node_modules/**",
            "**/__pycache__/**",
            "**/venv/**",
            "**/.venv/**",
            "**/site-packages/**",
            "**/.tox/**",
            "**/.git/**",
            "**/dist/**",
            "**/build/**",
            "**/target/**",
            "**/vendor/**",
            "**/.next/**",
            "**/coverage/**",
            "**/*.min.js",
            "**/*.test.*",
            "**/*.spec.*",
            # Example apps, documentation and benchmarks at the repository root: no
            # product code, and they would crowd the module diagram (Express ships 26
            # example apps beside one library). Root-only on purpose: Java packages
            # such as com/example/demo are product code.
            "examples/**",
            "example/**",
            "samples/**",
            "sample/**",
            "demo/**",
            "demos/**",
            "docs/**",
            "docs_src/**",
            "benchmarks/**",
            "benchmark/**",
        ]
    )
    max_depth: int = 5  # reserved, not used yet
    #: Add external systems found in third-party imports to L0 (see analyzers.external)
    detect_external_systems: bool = True
    # Directory levels below a source root (src/, src/main/java, go.mod dir, ...)
    # that name an L1 module. JS/TS workspace packages are always one module each.
    module_depth: int = 2


@dataclass
class DiagramConfig:
    output_dir: str = ".archilens/diagrams"
    format: str = "mermaid"
    levels: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"level": 0, "name": "System Context", "enabled": True},
            {"level": 1, "name": "Module Architecture", "enabled": True},
            {"level": 2, "name": "Component Detail", "enabled": True},
            {"level": 3, "name": "Process Flows", "enabled": True},
        ]
    )
    theme: str = "default"
    #: Most dependencies drawn on L1, heaviest first (0 = all); see generators.base.select_l1_edges
    l1_max_edges: int = 40
    #: Most components drawn on one L2 diagram, most connected first (0 = all); see generators.base.select_l2
    l2_max_components: int = 60


DEFAULT_MODEL = "claude-opus-5-5"
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class AIConfig:
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    # Reasoning effort for Claude 5-generation models: low | medium | high | xhigh | max.
    # None leaves the model's default. Ignored for older models and other providers.
    effort: str | None = "low"
    # Reuse AI answers for identical requests (~/.archilens/ai_cache.db)
    cache: bool = True
    features: dict[str, bool] = field(
        default_factory=lambda: {
            "process_flow_inference": True,
            "node_annotations": True,
            "capability_suggestions": True,
            "pattern_detection": True,
            "module_summaries": True,
        }
    )


#: Rule types, and the legacy rule names that selected a type before ``type`` existed.
RULE_TYPES = ("forbidden-dependency", "max-fan-out", "no-cycles")
_LEGACY_RULE_NAMES = {"no-layer-skip": "forbidden-dependency", "max-fan-out": "max-fan-out", "no-cycles": "no-cycles"}
_RULE_ACTIONS = ("warn", "fail")
_FAIL_ON = ("new", "all")


@dataclass
class CIRule:
    name: str
    action: str = "warn"
    from_pattern: str | None = None
    to_pattern: str | None = None
    threshold: int | None = None
    #: One of RULE_TYPES. Empty means "infer from a legacy name" (``no-layer-skip``, ``max-fan-out``).
    type: str = ""
    from_capability: str | None = None
    to_capability: str | None = None

    @property
    def kind(self) -> str:
        """The rule's type, falling back to the legacy name-based type."""
        return self.type or _LEGACY_RULE_NAMES.get(self.name, "")


@dataclass
class CIConfig:
    drift_detection: bool = True
    pr_comments: bool = True
    rules: list[CIRule] = field(default_factory=list)
    #: "new": on `archilens diff`, only violations not already on the base ref fail.
    #: "all": every violation of a "fail" rule fails, as in 0.3.0 and earlier.
    fail_on: str = "new"


@dataclass
class Capability:
    name: str
    description: str = ""
    modules: list[str] = field(default_factory=list)


@dataclass
class ExternalSystem:
    name: str
    type: str = "external_api"
    description: str = ""


@dataclass
class ArchiLensConfig:
    """Root configuration object."""

    project_name: str = "Unnamed Project"
    project_description: str = ""
    project_type: str = "monolith"

    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    diagrams: DiagramConfig = field(default_factory=DiagramConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    ci: CIConfig = field(default_factory=CIConfig)

    capabilities: list[Capability] = field(default_factory=list)
    external_systems: list[ExternalSystem] = field(default_factory=list)

    evolution_enabled: bool = True
    # Refs for `archilens history`; globs like "tags/v*" expand to tags. Empty = semver tags + HEAD.
    baseline_refs: list[str] = field(default_factory=list)


class ConfigError(Exception):
    """Raised when .archilens.yml is malformed or contains invalid values."""


def load_config(repo_path: str | Path) -> ArchiLensConfig:
    """
    Load ArchiLens configuration from a repository.

    Looks for .archilens.yml or .archilens.yaml in the repo root.
    Returns default config if no file is found.

    Raises:
        ConfigError: If the config file exists but cannot be parsed.
    """
    repo_path = Path(repo_path)

    for filename in [".archilens.yml", ".archilens.yaml"]:
        config_path = repo_path / filename
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
            except yaml.YAMLError as exc:
                raise ConfigError(
                    f"Could not parse {config_path.name}: {exc}\nCheck for tabs instead of spaces, or missing colons."
                ) from exc

            if not isinstance(raw, dict):
                raise ConfigError(
                    f"{config_path.name} must be a YAML mapping (key: value pairs), got {type(raw).__name__}."
                )

            try:
                return _parse_config(raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(
                    f"Invalid value in {config_path.name}: {exc}\n"
                    "Run 'python -m archilens init' to see the expected structure."
                ) from exc

    # No config file found — use defaults with auto-detection
    return ArchiLensConfig()


def _parse_choice(key: str, value: Any, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ValueError(f"{key} must be one of {', '.join(choices)}, got {value!r}")
    return str(value)


def _parse_rule(raw: Any) -> CIRule:
    """Parse and validate one ``ci.rules`` entry; a rule that can never fire is an error."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise ValueError(f"each ci.rules entry needs a name, got {raw!r}")
    rule = CIRule(
        name=raw["name"],
        action=_parse_choice(f"ci.rules[{raw['name']}].action", raw.get("action", "warn"), _RULE_ACTIONS),
        from_pattern=raw.get("from"),
        to_pattern=raw.get("to"),
        threshold=raw.get("threshold"),
        type=raw.get("type", ""),
        from_capability=raw.get("from_capability"),
        to_capability=raw.get("to_capability"),
    )
    where = f"ci.rules[{rule.name}]"
    if rule.kind not in RULE_TYPES:
        if rule.type:
            raise ValueError(f"{where}: unknown type {rule.type!r}; expected one of {', '.join(RULE_TYPES)}")
        raise ValueError(f"{where}: set 'type' to one of {', '.join(RULE_TYPES)}")
    if rule.kind == "forbidden-dependency" and not (
        (rule.from_pattern or rule.from_capability) and (rule.to_pattern or rule.to_capability)
    ):
        raise ValueError(
            f"{where}: forbidden-dependency needs 'from' or 'from_capability', and 'to' or 'to_capability'"
        )
    if rule.kind == "max-fan-out" and not (isinstance(rule.threshold, int) and rule.threshold > 0):
        raise ValueError(f"{where}: max-fan-out needs a positive integer 'threshold'")
    return rule


def _parse_module_depth(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"analysis.module_depth must be a positive integer, got {value!r}")
    return value


def _parse_effort(value: Any) -> str | None:
    """Validate ``ai.effort``; None (YAML null) means "use the model's default"."""
    if value is None:
        return None
    if value not in _EFFORT_LEVELS:
        raise ValueError(f"ai.effort must be one of {', '.join(_EFFORT_LEVELS)} or null, got {value!r}")
    return str(value)


def _unless_null(value: Any, default: Any) -> Any:
    """*value*, or *default* when the key is missing or left empty (YAML null); ``[]`` stays ``[]``."""
    return default if value is None else value


def _parse_config(raw: dict[str, Any]) -> ArchiLensConfig:
    """Parse raw YAML dict into typed config."""
    # `or {}` / `or []`: a section whose contents are all commented out is YAML null.
    project = raw.get("project") or {}
    analysis_raw = raw.get("analysis") or {}
    diagrams_raw = raw.get("diagrams") or {}
    ai_raw = raw.get("ai") or {}
    ci_raw = raw.get("ci") or {}
    evolution_raw = raw.get("evolution") or {}

    config = ArchiLensConfig(
        project_name=project.get("name", "Unnamed Project"),
        project_description=project.get("description", ""),
        project_type=project.get("type", "monolith"),
        analysis=AnalysisConfig(
            languages=analysis_raw.get("languages") or [],
            entry_points=analysis_raw.get("entry_points") or [],
            exclude=_unless_null(analysis_raw.get("exclude"), AnalysisConfig().exclude),
            max_depth=analysis_raw.get("max_depth", 5),
            detect_external_systems=bool(_unless_null(analysis_raw.get("detect_external_systems"), True)),
            module_depth=_parse_module_depth(analysis_raw.get("module_depth", 2)),
        ),
        diagrams=DiagramConfig(
            output_dir=diagrams_raw.get("output_dir", ".archilens/diagrams"),
            format=diagrams_raw.get("format", "mermaid"),
            levels=_unless_null(diagrams_raw.get("levels"), DiagramConfig().levels),
            theme=diagrams_raw.get("theme", "default"),
            l1_max_edges=int(diagrams_raw.get("l1_max_edges", 40)),
            l2_max_components=int(diagrams_raw.get("l2_max_components", 60)),
        ),
        ai=AIConfig(
            provider=ai_raw.get("provider", "anthropic"),
            model=ai_raw.get("model", DEFAULT_MODEL),
            effort=_parse_effort(ai_raw.get("effort", "low")),
            cache=bool(ai_raw.get("cache", True)),
            features=_unless_null(ai_raw.get("features"), AIConfig().features),
        ),
        ci=CIConfig(
            drift_detection=ci_raw.get("drift_detection", True),
            pr_comments=ci_raw.get("pr_comments", True),
            rules=[_parse_rule(r) for r in ci_raw.get("rules") or []],
            fail_on=_parse_choice("ci.fail_on", ci_raw.get("fail_on", "new"), _FAIL_ON),
        ),
        capabilities=[
            Capability(
                name=c["name"],
                description=c.get("description", ""),
                modules=c.get("modules", []),
            )
            for c in raw.get("capabilities") or []
        ],
        external_systems=[
            ExternalSystem(
                name=s["name"],
                type=s.get("type", "external_api"),
                description=s.get("description", ""),
            )
            for s in raw.get("external_systems") or []
        ],
        evolution_enabled=evolution_raw.get("enabled", True),
        baseline_refs=evolution_raw.get("baseline_refs") or [],
    )

    return config

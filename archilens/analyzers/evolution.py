"""
Git history evolution analysis.

Reads the repository's commit/tag history using GitPython and analyzes the
architecture at each configured ref *without* modifying the working tree —
all file reads happen via GitPython's object-database API (blob.data_stream).

Outputs:
  - A Mermaid timeline diagram showing module growth across releases
  - Per-ref ArchSnapshot objects for programmatic comparison
  - A markdown evolution report

Usage via CLI:
    archilens history --repo . --refs v1.0,v2.0,main
    archilens history --repo . --tags        # auto-pick all semver tags
"""

from __future__ import annotations

import fnmatch
import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from archilens.analyzers.dependencies import analyze_dependencies
from archilens.analyzers.discovery import discover_files
from archilens.models import EdgeType, NodeType

if TYPE_CHECKING:
    from archilens.config import ArchiLensConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RefSnapshot:
    """
    Architecture at a single git ref, from the same analysis as ``archilens analyze``
    (discovery, module layout and import resolution), without AI.
    """

    ref: str
    timestamp: str
    module_count: int
    node_count: int  # modules + components (classes and files of functions)
    edge_count: int  # module dependencies
    total_loc: int
    modules: dict[str, int] = field(default_factory=dict)  # module path -> LOC
    patterns: list[str] = field(default_factory=list)
    commit_sha: str = ""
    commit_message: str = ""
    file_count: int = 0
    dependencies: list[tuple[str, str]] = field(default_factory=list)  # (source module, target module)


@dataclass
class EvolutionTimeline:
    """Collection of architecture snapshots across git history."""

    project_name: str
    refs: list[RefSnapshot] = field(default_factory=list)

    # Modules that were added / removed between consecutive refs
    additions: list[tuple[str, str]] = field(default_factory=list)  # (module, ref)
    removals: list[tuple[str, str]] = field(default_factory=list)  # (module, ref)

    # Module dependencies that appeared / disappeared between consecutive refs
    dependency_additions: list[tuple[str, str, str]] = field(default_factory=list)  # (source, target, ref)
    dependency_removals: list[tuple[str, str, str]] = field(default_factory=list)  # (source, target, ref)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def analyze_evolution(
    repo_path: Path,
    config: ArchiLensConfig,
    refs: list[str] | None = None,
    use_tags: bool = False,
    max_refs: int = 10,
) -> EvolutionTimeline:
    """
    Analyze architecture evolution across multiple git refs.

    Args:
        repo_path:  Repository root.
        config:     ArchiLensConfig (used for language filters, excludes, etc.).
        refs:       Explicit list of git refs to analyze. If None and
                    *use_tags* is True, picks all semver tags. Otherwise
                    ``evolution.baseline_refs`` from the config, where a
                    pattern such as ``tags/v*`` expands to matching tags;
                    refs that don't exist are skipped, and if none do, the
                    semver tags are used. Without explicit refs, HEAD is
                    appended when it differs from the last ref, so the
                    timeline ends at the current code.
        use_tags:   Auto-discover semver tags from the repo.
        max_refs:   Cap the number of refs analysed (most recent first).

    Returns:
        EvolutionTimeline with per-ref snapshots and diff info.
    """
    try:
        import git
    except ImportError as exc:
        raise RuntimeError("gitpython is required for evolution analysis. Install with: pip install gitpython") from exc

    try:
        repo = git.Repo(repo_path, search_parent_directories=True)
    except git.InvalidGitRepositoryError as exc:
        raise RuntimeError(f"{repo_path} is not a Git repository.") from exc

    # Resolve which refs to analyse
    explicit = refs is not None
    if refs is None:
        refs = _discover_semver_tags(repo) if use_tags else _expand_refs(repo, list(config.baseline_refs))
        if not refs and not use_tags:
            refs = _discover_semver_tags(repo)
        refs = refs[-(max_refs - 1) :] if max_refs > 1 else refs[-1:]
        if not refs or _sha(repo, refs[-1]) != _sha(repo, "HEAD"):
            refs.append("HEAD")
    if not refs:
        refs = ["HEAD"]

    # Keep the most recent *max_refs* (refs are oldest first)
    refs = refs[-max_refs:] if explicit else refs

    logger.info("Analysing evolution across %d refs: %s", len(refs), refs)

    timeline = EvolutionTimeline(project_name=config.project_name)
    prev_modules: set[str] = set()
    prev_deps: set[tuple[str, str]] | None = None

    for ref in refs:
        logger.info("  → %s", ref)
        snap = _analyse_ref(repo, repo_path, ref, config)
        if snap is None:
            logger.warning("  Skipping %s (could not resolve)", ref)
            continue

        timeline.refs.append(snap)

        current_modules = set(snap.modules.keys())
        for mod in sorted(current_modules - prev_modules):
            timeline.additions.append((mod, ref))
        for mod in sorted(prev_modules - current_modules):
            timeline.removals.append((mod, ref))
        prev_modules = current_modules

        current_deps = set(snap.dependencies)
        if prev_deps is not None:  # the first ref has nothing to compare against
            for src, tgt in sorted(current_deps - prev_deps):
                timeline.dependency_additions.append((src, tgt, ref))
            for src, tgt in sorted(prev_deps - current_deps):
                timeline.dependency_removals.append((src, tgt, ref))
        prev_deps = current_deps

    return timeline


# ---------------------------------------------------------------------------
# Diagram generators
# ---------------------------------------------------------------------------


def generate_timeline_diagram(timeline: EvolutionTimeline) -> str:
    """
    Generate a Mermaid timeline diagram from an EvolutionTimeline.

    Uses the ``timeline`` diagram type (Mermaid v10+).
    Falls back to a ``xychart-beta`` bar chart showing LOC growth if
    fewer than 2 refs are available.
    """
    if len(timeline.refs) < 2:
        return _single_ref_note(timeline)

    lines: list[str] = [
        f"# Architecture Evolution — {timeline.project_name}",
        "",
        "## Module Growth Over Time",
        "",
        "```mermaid",
        "timeline",
        f"    title {timeline.project_name} — Architecture Evolution",
    ]

    for snap in timeline.refs:
        ts_label = snap.timestamp[:10] if snap.timestamp else snap.ref
        # Shorten label: "v1.2.3 (2024-01-15)"
        header = f"{snap.ref} ({ts_label})"
        lines.append(f"    section {header}")
        # Show top 5 modules by LOC
        top = sorted(snap.modules.items(), key=lambda x: x[1], reverse=True)[:5]
        for mod_name, loc in top:
            lines.append(f"        {mod_name} : {loc:,} LOC")

    lines.append("```")
    lines.append("")

    # LOC growth chart
    lines += _loc_growth_chart(timeline)

    # Module additions/removals table
    lines += _change_table(timeline)

    # Per-ref metrics table
    lines += _metrics_table(timeline)

    return "\n".join(lines)


def generate_evolution_report(timeline: EvolutionTimeline) -> str:
    """Generate a detailed markdown report of architecture evolution."""
    parts: list[str] = [
        f"# ArchiLens Evolution Report — {timeline.project_name}",
        "",
        f"Analysed **{len(timeline.refs)}** refs from git history.",
        "",
    ]

    if not timeline.refs:
        parts.append("*No refs could be analysed.*")
        return "\n".join(parts)

    # Summary stats
    first, last = timeline.refs[0], timeline.refs[-1]
    loc_delta = last.total_loc - first.total_loc
    mod_delta = last.module_count - first.module_count
    sign = "+" if loc_delta >= 0 else ""

    parts += [
        "## Summary",
        "",
        f"| Metric | {first.ref} | {last.ref} | Change |",
        "|--------|--------|----|--------|",
        f"| Modules | {first.module_count} | {last.module_count} | {'+' if mod_delta >= 0 else ''}{mod_delta} |",
        f"| Modules + components | {first.node_count} | {last.node_count} | {'+' if last.node_count - first.node_count >= 0 else ''}{last.node_count - first.node_count} |",
        f"| Module dependencies | {first.edge_count} | {last.edge_count} | {'+' if last.edge_count - first.edge_count >= 0 else ''}{last.edge_count - first.edge_count} |",
        f"| Total LOC | {first.total_loc:,} | {last.total_loc:,} | {sign}{loc_delta:,} |",
        "",
    ]

    # New modules
    if timeline.additions:
        parts.append("## New Modules (chronological)")
        parts.append("")
        for mod, ref in timeline.additions:
            parts.append(f"- **{mod}** — introduced at `{ref}`")
        parts.append("")

    # Removed modules
    if timeline.removals:
        parts.append("## Removed Modules")
        parts.append("")
        for mod, ref in timeline.removals:
            parts.append(f"- **{mod}** — removed at `{ref}`")
        parts.append("")

    # Dependency changes between consecutive refs
    if timeline.dependency_additions or timeline.dependency_removals:
        parts.append("## Dependency Changes")
        parts.append("")
        parts.append("| At ref | Change | Dependency |")
        parts.append("|--------|--------|------------|")
        changes = [(ref, "added", src, tgt) for src, tgt, ref in timeline.dependency_additions]
        changes += [(ref, "removed", src, tgt) for src, tgt, ref in timeline.dependency_removals]
        order = {snap.ref: i for i, snap in enumerate(timeline.refs)}
        for ref, change, src, tgt in sorted(changes, key=lambda c: (order.get(c[0], 0), c[1], c[2], c[3])):
            parts.append(f"| `{ref}` | {change} | {src} -> {tgt} |")
        parts.append("")

    # Per-ref detail
    parts.append("## Per-Ref Detail")
    parts.append("")
    for snap in timeline.refs:
        parts.append(f"### `{snap.ref}`")
        if snap.commit_message:
            parts.append(f"*{snap.commit_message[:100]}*")
        parts.append("")
        parts.append(f"- **Modules:** {snap.module_count}")
        parts.append(f"- **Source files:** {snap.file_count}  |  **Modules + components:** {snap.node_count}")
        parts.append(f"- **Module dependencies:** {snap.edge_count}")
        parts.append(f"- **Total LOC:** {snap.total_loc:,}")
        if snap.patterns:
            parts.append(f"- **Patterns:** {', '.join(snap.patterns)}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _analyse_ref(repo: Any, repo_path: Path, ref: str, config: ArchiLensConfig) -> RefSnapshot | None:
    """
    Analyse the architecture at *ref*.

    The ref's source files (and the manifests import resolution reads:
    ``package.json``, ``tsconfig.json``, ``go.mod``) are written from the git
    object database into a temporary directory, which then goes through the
    same discovery and dependency analysis as ``archilens analyze``. The working
    tree is never touched.
    """
    try:
        commit = repo.commit(ref)
    except Exception as exc:
        logger.debug("Cannot resolve ref %s: %s", ref, exc)
        return None

    ts = datetime.fromtimestamp(commit.committed_date).strftime("%Y-%m-%d")
    lang_exts = _get_lang_extensions(config)

    with tempfile.TemporaryDirectory(prefix="archilens_history_") as tmp:
        root = Path(tmp)
        try:
            for blob in commit.tree.traverse():
                if blob.type != "blob" or not _needed_for_analysis(blob.path, lang_exts):
                    continue
                size = getattr(blob, "size", 0)
                if isinstance(size, int) and size > _MAX_BLOB_BYTES:
                    continue  # generated or vendored files; not architecture
                dest = root / blob.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob.data_stream.read())
        except Exception as exc:
            logger.debug("Tree traversal failed for %s: %s", ref, exc)

        files, modules = discover_files(root, config)
        nodes, edges, _graph = analyze_dependencies(files, modules, root)

    module_nodes = [n for n in nodes if n.node_type == NodeType.MODULE]
    module_loc = {n.id.removeprefix("module:"): n.lines_of_code for n in module_nodes}
    dependencies = sorted(
        (e.source.removeprefix("module:"), e.target.removeprefix("module:"))
        for e in edges
        if e.edge_type == EdgeType.DEPENDENCY and e.source.startswith("module:")  # not imports within a module
    )

    return RefSnapshot(
        ref=ref,
        timestamp=ts,
        module_count=len(module_loc),
        node_count=len(nodes),
        edge_count=len(dependencies),
        total_loc=sum(module_loc.values()),
        modules=module_loc,
        patterns=_detect_patterns_lightweight(set(module_loc)),
        commit_sha=commit.hexsha[:8],
        commit_message=commit.message.split("\n")[0].strip(),
        file_count=sum(1 for f in files if not f.is_test),
        dependencies=dependencies,
    )


_MAX_BLOB_BYTES = 2_000_000
_MANIFESTS = {"package.json", "go.mod", "tsconfig.json", "jsconfig.json"}


def _needed_for_analysis(path: str, lang_exts: set[str]) -> bool:
    name = Path(path).name
    return (
        Path(path).suffix.lower() in lang_exts
        or name in _MANIFESTS
        or (name.startswith("tsconfig") and name.endswith(".json"))
    )


def _expand_refs(repo: Any, patterns: list[str]) -> list[str]:
    """
    Expand configured refs: a glob such as ``tags/v*`` or ``release-*`` becomes
    the matching tags (in semver order), then matching branches. Refs that don't
    resolve are dropped.
    """
    tags = _discover_semver_tags(repo) + sorted(
        t.name for t in repo.tags if t.name not in set(_discover_semver_tags(repo))
    )
    branches = sorted(h.name for h in getattr(repo, "heads", []))
    expanded: list[str] = []
    for pattern in patterns:
        name = pattern.removeprefix("tags/").removeprefix("refs/tags/")
        if any(c in name for c in "*?["):
            expanded += [t for t in tags if fnmatch.fnmatch(t, name)]
            expanded += [b for b in branches if fnmatch.fnmatch(b, name)]
        else:
            expanded.append(pattern)
    resolvable = [r for r in dict.fromkeys(expanded) if _sha(repo, r)]
    for skipped in [r for r in expanded if r not in resolvable]:
        logger.info("  Skipping %s (no such ref)", skipped)
    return resolvable


def _sha(repo: Any, ref: str) -> str | None:
    try:
        return str(repo.commit(ref).hexsha)
    except Exception:
        return None


def _discover_semver_tags(repo: Any) -> list[str]:
    """Return all semver tags sorted oldest→newest."""
    _semver = re.compile(r"^v?\d+\.\d+")
    tags = [t.name for t in repo.tags if _semver.match(t.name)]
    # Natural sort
    tags.sort(key=lambda t: [_try_int(p) for p in re.split(r"[.\-]", t.lstrip("v"))])
    return tags


def _try_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def _get_lang_extensions(config: ArchiLensConfig) -> set[str]:
    from archilens.analyzers.discovery import EXTENSION_MAP

    if config.analysis.languages:
        return {ext for ext, lang in EXTENSION_MAP.items() if lang in config.analysis.languages}
    return set(EXTENSION_MAP.keys())


def _is_source_file(path: str, lang_exts: set[str], excludes: list[str]) -> bool:
    import fnmatch

    suffix = Path(path).suffix.lower()
    if suffix not in lang_exts:
        return False
    for pat in excludes:
        clean = pat.replace("**", "*")
        if fnmatch.fnmatch(path, clean) or fnmatch.fnmatch(Path(path).name, clean):
            return False
    return True


def _detect_patterns_lightweight(module_names: set[str]) -> list[str]:
    """Heuristic pattern detection from module naming conventions."""
    detected: list[str] = []
    names_lower = {n.lower() for n in module_names}

    if any("controller" in n or "view" in n or "model" in n for n in names_lower):
        detected.append("mvc")
    if any("event" in n or "message" in n or "queue" in n for n in names_lower):
        detected.append("event_driven")
    if any("repository" in n or "repo" in n for n in names_lower):
        detected.append("repository")
    if any("service" in n for n in names_lower) and any("handler" in n or "api" in n for n in names_lower):
        detected.append("layered")

    return detected


# ---------------------------------------------------------------------------
# Diagram helpers
# ---------------------------------------------------------------------------


def _single_ref_note(timeline: EvolutionTimeline) -> str:
    if not timeline.refs:
        return f"# Evolution — {timeline.project_name}\n\n*No refs to compare.*\n"
    snap = timeline.refs[0]
    return (
        f"# Evolution — {timeline.project_name}\n\n"
        f"Only one ref analysed: `{snap.ref}` ({snap.module_count} modules, "
        f"{snap.total_loc:,} LOC).\n\n"
        "Run with multiple tags or refs to see the evolution timeline.\n"
    )


def _loc_growth_chart(timeline: EvolutionTimeline) -> list[str]:
    """Mermaid xychart-beta showing total LOC per ref."""
    if len(timeline.refs) < 2:
        return []

    lines: list[str] = [
        "## Lines of Code Growth",
        "",
        "```mermaid",
        "xychart-beta",
        '    title "Total LOC per Release"',
        "    x-axis [" + ", ".join(f'"{r.ref}"' for r in timeline.refs) + "]",
        '    y-axis "LOC"',
        "    bar [" + ", ".join(str(r.total_loc) for r in timeline.refs) + "]",
        "```",
        "",
    ]
    return lines


def _change_table(timeline: EvolutionTimeline) -> list[str]:
    if not timeline.additions and not timeline.removals:
        return []

    lines: list[str] = [
        "## Module Changes",
        "",
        "| Change | Module | At Ref |",
        "|--------|--------|--------|",
    ]
    for mod, ref in timeline.additions:
        lines.append(f"| Added | {mod} | `{ref}` |")
    for mod, ref in timeline.removals:
        lines.append(f"| Removed | {mod} | `{ref}` |")
    lines.append("")
    return lines


def _metrics_table(timeline: EvolutionTimeline) -> list[str]:
    lines: list[str] = [
        "## Metrics Per Ref",
        "",
        "| Ref | Commit | Modules | LOC | Modules + components | Dependencies | Patterns |",
        "|-----|--------|---------|-----|-------|-------|----------|",
    ]
    for snap in timeline.refs:
        patterns = ", ".join(snap.patterns) or "—"
        lines.append(
            f"| `{snap.ref}` | {snap.commit_sha} "
            f"| {snap.module_count} | {snap.total_loc:,} "
            f"| {snap.node_count} | {snap.edge_count} | {patterns} |"
        )
    lines.append("")
    return lines

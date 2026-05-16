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

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RefSnapshot:
    """Architecture metrics captured at a single git ref."""
    ref: str
    timestamp: str
    module_count: int
    node_count: int
    edge_count: int
    total_loc: int
    modules: dict[str, int] = field(default_factory=dict)  # name -> LOC
    patterns: list[str] = field(default_factory=list)
    commit_sha: str = ""
    commit_message: str = ""


@dataclass
class EvolutionTimeline:
    """Collection of architecture snapshots across git history."""
    project_name: str
    refs: list[RefSnapshot] = field(default_factory=list)

    # Modules that were added / removed between consecutive refs
    additions: list[tuple[str, str]] = field(default_factory=list)   # (module, ref)
    removals: list[tuple[str, str]] = field(default_factory=list)    # (module, ref)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_evolution(
    repo_path: Path,
    config,  # ArchiLensConfig
    refs: Optional[list[str]] = None,
    use_tags: bool = False,
    max_refs: int = 10,
) -> EvolutionTimeline:
    """
    Analyze architecture evolution across multiple git refs.

    Args:
        repo_path:  Repository root.
        config:     ArchiLensConfig (used for language filters, excludes, etc.).
        refs:       Explicit list of git refs to analyze.  If None and
                    *use_tags* is True, picks all semver tags.  If both are
                    falsy, uses ``config.baseline_refs``.
        use_tags:   Auto-discover semver tags from the repo.
        max_refs:   Cap the number of refs analysed (most recent first).

    Returns:
        EvolutionTimeline with per-ref snapshots and diff info.
    """
    try:
        import git  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "gitpython is required for evolution analysis. "
            "Install with: pip install gitpython"
        )

    try:
        repo = git.Repo(repo_path, search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        raise RuntimeError(f"{repo_path} is not a Git repository.")

    # Resolve which refs to analyse
    if refs is None:
        if use_tags:
            refs = _discover_semver_tags(repo)
        else:
            refs = list(config.baseline_refs) if config.baseline_refs else ["HEAD"]

    if not refs:
        refs = ["HEAD"]

    # Cap and most-recent-first (tags are already sorted ascending — reverse)
    refs = refs[-max_refs:]

    logger.info("Analysing evolution across %d refs: %s", len(refs), refs)

    timeline = EvolutionTimeline(project_name=config.project_name)
    prev_modules: set[str] = set()

    for ref in refs:
        logger.info("  → %s", ref)
        snap = _analyse_ref(repo, repo_path, ref, config)
        if snap is None:
            logger.warning("  Skipping %s (could not resolve)", ref)
            continue

        timeline.refs.append(snap)

        current_modules = set(snap.modules.keys())
        for mod in current_modules - prev_modules:
            timeline.additions.append((mod, ref))
        for mod in prev_modules - current_modules:
            timeline.removals.append((mod, ref))

        prev_modules = current_modules

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
        f"| Nodes | {first.node_count} | {last.node_count} | {'+' if last.node_count - first.node_count >= 0 else ''}{last.node_count - first.node_count} |",
        f"| Edges | {first.edge_count} | {last.edge_count} | {'+' if last.edge_count - first.edge_count >= 0 else ''}{last.edge_count - first.edge_count} |",
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

    # Per-ref detail
    parts.append("## Per-Ref Detail")
    parts.append("")
    for snap in timeline.refs:
        parts.append(f"### `{snap.ref}`")
        if snap.commit_message:
            parts.append(f"*{snap.commit_message[:100]}*")
        parts.append("")
        parts.append(f"- **Modules:** {snap.module_count}")
        parts.append(f"- **Nodes:** {snap.node_count}  |  **Edges:** {snap.edge_count}")
        parts.append(f"- **Total LOC:** {snap.total_loc:,}")
        if snap.patterns:
            parts.append(f"- **Patterns:** {', '.join(snap.patterns)}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _analyse_ref(repo, repo_path: Path, ref: str, config) -> Optional[RefSnapshot]:
    """
    Analyse the architecture at *ref* by reading blobs from the git object db.

    No working-tree checkout is performed — all reads go through GitPython's
    blob.data_stream API, keeping the working tree pristine.
    """
    try:
        commit = repo.commit(ref)
    except Exception as exc:
        logger.debug("Cannot resolve ref %s: %s", ref, exc)
        return None

    ts = datetime.fromtimestamp(commit.committed_date).strftime("%Y-%m-%d")

    # Walk the commit tree and collect source files
    modules: dict[str, int] = {}  # module_name -> total LOC
    total_nodes = 0
    total_edges = 0

    lang_exts = _get_lang_extensions(config)
    exclude_patterns = config.analysis.exclude if config.analysis else []

    try:
        for blob in commit.tree.traverse():
            if blob.type != "blob":
                continue

            path = blob.path  # relative path within repo
            if not _is_source_file(path, lang_exts, exclude_patterns):
                continue

            # Determine module (first 1-2 path components)
            parts = Path(path).parts
            if len(parts) == 1:
                module_name = "<root>"
            else:
                module_name = "/".join(parts[:min(2, len(parts) - 1)])

            # Count lines of code
            try:
                content = blob.data_stream.read().decode("utf-8", errors="ignore")
                loc = content.count("\n") + 1
                modules[module_name] = modules.get(module_name, 0) + loc
                total_nodes += 1
                # Rough edge estimate: count import lines
                total_edges += sum(
                    1 for line in content.splitlines()
                    if line.strip().startswith(("import ", "from ", "require(", "use "))
                )
            except Exception:
                continue

    except Exception as exc:
        logger.debug("Tree traversal failed for %s: %s", ref, exc)

    # Detected patterns — lightweight: check directory/file naming
    patterns = _detect_patterns_lightweight(set(modules.keys()))

    return RefSnapshot(
        ref=ref,
        timestamp=ts,
        module_count=len(modules),
        node_count=total_nodes,
        edge_count=total_edges,
        total_loc=sum(modules.values()),
        modules=modules,
        patterns=patterns,
        commit_sha=commit.hexsha[:8],
        commit_message=commit.message.split("\n")[0].strip(),
    )


def _discover_semver_tags(repo) -> list[str]:
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


def _get_lang_extensions(config) -> set[str]:
    from archilens.analyzers.discovery import EXTENSION_MAP
    if config.analysis.languages:
        return {
            ext for ext, lang in EXTENSION_MAP.items()
            if lang in config.analysis.languages
        }
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
        '    x-axis [' + ", ".join(f'"{r.ref}"' for r in timeline.refs) + "]",
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
        "| Ref | Commit | Modules | LOC | Nodes | Edges | Patterns |",
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

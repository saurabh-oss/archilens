"""
Main orchestrator for ArchiLens analysis pipeline.

Coordinates the full flow: discovery → static analysis → AI augmentation
→ diagram generation → output.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from archilens.ai import (
    AIClient,
    detect_patterns,
    generate_module_summaries,
    infer_process_flows,
    module_outline,
    review_architecture_diff,
)
from archilens.ai.cache import AICache
from archilens.analyzers.dependencies import analyze_dependencies, compute_metrics, unanalyzed_languages
from archilens.analyzers.discovery import Module, SourceFile, discover_files
from archilens.analyzers.embedded import script_source
from archilens.analyzers.external import detect_external_systems
from archilens.config import ArchiLensConfig, load_config
from archilens.generators.base import get_generator
from archilens.models import ArchDiff, ArchEdge, ArchNode, ArchSnapshot
from archilens.utils import module_matches

if TYPE_CHECKING:
    import networkx as nx

logger = logging.getLogger(__name__)

#: Notes already logged in this process: `diff` and `history` analyse several refs of one repository.
_UNANALYZED_WARNED: set[str] = set()


def analyze_repository(
    repo_path: str | Path,
    config: ArchiLensConfig | None = None,
    use_ai: bool = True,
    git_ref: str = "HEAD",
) -> ArchSnapshot:
    """
    Run the full ArchiLens analysis pipeline on a repository.

    Pipeline stages:
    1. Load configuration
    2. Discover source files and modules
    3. Extract dependencies via static analysis
    4. (Optional) AI-powered process flow inference
    5. (Optional) AI-powered annotations and pattern detection
    6. Build the architecture snapshot

    Args:
        repo_path: Path to the Git repository root
        config: Optional config (loaded from .archilens.yml if None)
        use_ai: Whether to enable AI-powered features
        git_ref: Git reference being analyzed

    Returns:
        Complete ArchSnapshot with all analysis results
    """
    repo_path = Path(repo_path).resolve()

    # Stage 1: Configuration
    if config is None:
        config = load_config(repo_path)
    logger.info(f"Analyzing: {config.project_name} at {repo_path}")

    # Stage 2: File Discovery
    logger.info("Stage 2: Discovering source files...")
    files, modules = discover_files(repo_path, config)
    logger.info(f"  Found {len(files)} source files across {len(modules)} modules")

    if not files:
        logger.warning("No source files found. Check your config exclude patterns.")
        return ArchSnapshot(
            project_name=config.project_name,
            git_ref=git_ref,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Stage 3: Static Analysis
    logger.info("Stage 3: Analyzing dependencies...")
    nodes, edges, graph = _analyze_with_progress(files, modules, repo_path)
    logger.info(f"  Built graph: {len(nodes)} nodes, {len(edges)} edges")

    # Compute metrics
    metrics = compute_metrics(graph)
    for node in nodes:
        if node.id in metrics:
            node.complexity = metrics[node.id].get("betweenness", 0.0)

    # Build initial snapshot
    snapshot = ArchSnapshot(
        project_name=config.project_name,
        project_type=config.project_type,
        git_ref=git_ref,
        timestamp=datetime.now(timezone.utc).isoformat(),
        nodes=nodes,
        edges=edges,
        unanalyzed=unanalyzed_languages(files),
    )
    if config.analysis.detect_external_systems:
        snapshot.detected_systems = detect_external_systems(
            graph.graph.get("unresolved_imports", []), [(e.name, e.type) for e in config.external_systems]
        )
        if snapshot.detected_systems:
            logger.info("  Detected external systems: " + ", ".join(s.name for s in snapshot.detected_systems))
    # Say so once, instead of quietly drawing modules with nothing between them.
    note = snapshot.unanalyzed_note()
    if note and note not in _UNANALYZED_WARNED:
        _UNANALYZED_WARNED.add(note)
        logger.warning("%s.", note)

    # Apply business capability mappings from config
    _apply_capability_mappings(snapshot, config)

    # Stage 4 & 5: AI Augmentation
    if use_ai and any(config.ai.features.values()):
        logger.info("Stage 4: AI-powered analysis...")

        ai_client = AIClient(config.ai, cache=AICache() if config.ai.cache else None)
        not_ready = ai_client.check_ready()
        if not_ready:
            # One clear line instead of a warning per module; the static analysis is still complete.
            logger.warning("Skipping AI analysis: %s. Pass --no-ai to skip AI explicitly.", not_ready)
            return snapshot

        try:
            # Process flow inference
            if config.ai.features.get("process_flow_inference", False):
                entry_points = [
                    {
                        "path": f.relative_path,
                        "language": f.language,
                        "entry_type": f.entry_type or "entry_point",
                        "dependencies": "",
                    }
                    for f in files
                    if f.is_entry_point and not f.is_test
                ]

                if not entry_points:
                    logger.info(
                        "  No entry points found, so no process flows. List them under "
                        "analysis.entry_points in .archilens.yml."
                    )
                else:
                    source = "configured" if config.analysis.entry_points else "detected"
                    logger.info(
                        f"  Inferring process flows from {len(entry_points)} {source} entry points: "
                        + ", ".join(ep["path"] for ep in entry_points)
                    )
                    flows = infer_process_flows(entry_points, ai_client, repo_path)
                    snapshot.flows = flows
                    logger.info(f"  Generated {len(flows)} process flows")

            # Module summaries
            if config.ai.features.get("module_summaries", False):
                logger.info("  Generating module summaries...")
                summaries = generate_module_summaries(snapshot, ai_client, outlines=_module_outlines(files))
                logger.info(f"  Summarized {len(summaries)} modules")

            # Pattern detection
            if config.ai.features.get("pattern_detection", False):
                logger.info("  Detecting architectural patterns...")
                dir_tree = _build_directory_tree(repo_path, max_depth=3)
                patterns = detect_patterns(snapshot, dir_tree, ai_client)
                snapshot.detected_patterns = patterns
                logger.info(f"  Detected patterns: {[p.value for p in patterns]}")

        except Exception as e:
            logger.warning(f"AI analysis failed (continuing without): {e}")

        if ai_client.usage.requests or ai_client.usage.cached:
            logger.info(f"  AI usage: {ai_client.usage.text()}")

    return snapshot


def generate_diagrams(
    snapshot: ArchSnapshot,
    config: ArchiLensConfig,
    output_dir: str | Path | None = None,
) -> list[Path]:
    """Generate all diagrams from an architecture snapshot."""
    if output_dir is None:
        output_dir = config.diagrams.output_dir

    logger.info(f"Generating diagrams to {output_dir} (format: {config.diagrams.format})...")
    generator = get_generator(config)
    generated = generator.generate_all(snapshot, output_dir)
    logger.info(f"Generated {len(generated)} diagram files")

    return generated


#: AI flows per review: each one is a request, and a PR rarely adds more than one entry point.
_MAX_REVIEW_FLOWS = 2


def review_diff(
    diff: ArchDiff,
    head: ArchSnapshot,
    head_dir: Path,
    config: ArchiLensConfig,
    changed: list[tuple[str, str]],
) -> str | None:
    """
    Add an AI review to *diff* (``diff.review``): a summary of the architectural
    impact, points worth a reviewer's attention, a description of each new module,
    and the process flow of each entry-point file the change adds (at most two).

    *head_dir* is the head ref's tree on disk; *changed* is ``(git status, path)``
    for the files that differ. Returns None on success, otherwise a one-line
    reason the review was skipped; the static diff is complete either way.
    """
    client = AIClient(config.ai, cache=AICache() if config.ai.cache else None)
    not_ready = client.check_ready()
    if not_ready:
        return not_ready

    files, _ = discover_files(head_dir, config)
    added_modules = {e.entity_id for e in diff.entries if e.change_type == "added" and e.entity_type == "node"}
    outlines = _module_outlines([f for f in files if f"module:{f.module}" in added_modules])
    review = review_architecture_diff(diff, head, changed, outlines, client)
    if review is None:
        return "the AI review request failed"

    added_files = {path for status, path in changed if status == "A"}
    new_entry_points = [f for f in files if f.is_entry_point and not f.is_test and f.relative_path in added_files]
    if new_entry_points and config.ai.features.get("process_flow_inference", False):
        review.flows = infer_process_flows(
            [
                {"path": f.relative_path, "language": f.language, "entry_type": f.entry_type or "entry_point"}
                for f in new_entry_points[:_MAX_REVIEW_FLOWS]
            ],
            client,
            head_dir,
        )
    review.usage = client.usage.model_copy()
    diff.review = review
    return None


def run_full_pipeline(
    repo_path: str | Path,
    use_ai: bool = True,
    output_dir: str | Path | None = None,
) -> tuple[ArchSnapshot, list[Path]]:
    """
    Convenience function: run analysis + generate diagrams.

    Returns:
        Tuple of (snapshot, list of generated file paths)
    """
    config = load_config(repo_path)
    snapshot = analyze_repository(repo_path, config, use_ai=use_ai)
    diagrams = generate_diagrams(snapshot, config, output_dir)
    return snapshot, diagrams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _analyze_with_progress(
    files: list[SourceFile],
    modules: dict[str, Module],
    repo_path: Path,
) -> tuple[list[ArchNode], list[ArchEdge], nx.DiGraph]:
    """Run dependency analysis with a rich progress bar for large repos."""

    # Only show the progress bar for repos with enough files to matter
    if len(files) < 100:
        return analyze_dependencies(files, modules, repo_path)

    try:
        from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[detail]}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Analyzing dependencies...", total=len(files), detail="")

            def _on_file(relative_path: str) -> None:
                progress.update(task, advance=1, detail=relative_path[-40:])

            return analyze_dependencies(files, modules, repo_path, progress_cb=_on_file)
    except ImportError:
        return analyze_dependencies(files, modules, repo_path)


def _module_outlines(files: list[SourceFile]) -> dict[str, str]:
    """Signature outline per module ID, from its non-test files."""
    by_module: dict[str, list[tuple[str, str, str]]] = {}
    for f in files:
        if f.is_test:
            continue
        try:
            text = f.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        code, code_language = script_source(text, f.language)  # Vue/Svelte: the <script> blocks
        by_module.setdefault(f"module:{f.module}", []).append((f.relative_path, code_language, code))
    return {module_id: module_outline(entries) for module_id, entries in by_module.items()}


def _apply_capability_mappings(
    snapshot: ArchSnapshot,
    config: ArchiLensConfig,
) -> None:
    """Map modules to business capabilities based on config."""
    for cap in config.capabilities:
        matched_modules: list[str] = []
        for node in snapshot.get_module_nodes():
            module_path = node.file_path or node.id.replace("module:", "")
            for pattern in cap.modules:
                if module_matches(module_path, pattern):
                    matched_modules.append(node.id)
                    node.capability = cap.name
                    break

        if matched_modules:
            snapshot.capability_map[cap.name] = matched_modules


def _build_directory_tree(repo_path: Path, max_depth: int = 3) -> str:
    """Build a text representation of the directory structure."""
    lines: list[str] = []

    def _walk(path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))

        # Filter out hidden and common noise directories
        skip = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}
        entries = [e for e in entries if e.name not in skip and not e.name.startswith(".")]

        for i, entry in enumerate(entries[:30]):  # Cap at 30 entries per level
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)

    lines.append(repo_path.name + "/")
    _walk(repo_path, "", 0)
    return "\n".join(lines)

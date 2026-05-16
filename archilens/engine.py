"""
Main orchestrator for ArchiLens analysis pipeline.

Coordinates the full flow: discovery → static analysis → AI augmentation
→ diagram generation → output.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import networkx as nx

from archilens.ai import AIClient, detect_patterns, generate_module_summaries, infer_process_flows
from archilens.analyzers.dependencies import analyze_dependencies, compute_metrics
from archilens.analyzers.discovery import discover_files
from archilens.config import ArchiLensConfig, load_config
from archilens.generators.base import get_generator
from archilens.models import ArchSnapshot, DiagramLevel

logger = logging.getLogger(__name__)


def analyze_repository(
    repo_path: str | Path,
    config: Optional[ArchiLensConfig] = None,
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
    logger.info(
        f"  Found {len(files)} source files across {len(modules)} modules"
    )
    
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
        version=config.project_type,
        git_ref=git_ref,
        timestamp=datetime.now(timezone.utc).isoformat(),
        nodes=nodes,
        edges=edges,
    )
    
    # Apply business capability mappings from config
    _apply_capability_mappings(snapshot, config)
    
    # Stage 4 & 5: AI Augmentation
    if use_ai and any(config.ai.features.values()):
        logger.info("Stage 4: AI-powered analysis...")
        
        try:
            ai_client = AIClient(config.ai)
            
            # Process flow inference
            if config.ai.features.get("process_flow_inference", False):
                entry_points = [
                    {
                        "path": f.relative_path,
                        "language": f.language,
                        "entry_type": "http_handler",
                        "dependencies": "",
                    }
                    for f in files
                    if f.is_entry_point and not f.is_test
                ]
                
                if entry_points:
                    logger.info(f"  Inferring process flows from {len(entry_points)} entry points...")
                    flows = infer_process_flows(entry_points, ai_client, repo_path)
                    snapshot.flows = flows
                    logger.info(f"  Generated {len(flows)} process flows")
            
            # Module summaries
            if config.ai.features.get("module_summaries", False):
                logger.info("  Generating module summaries...")
                summaries = generate_module_summaries(snapshot, ai_client)
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
    
    return snapshot


def generate_diagrams(
    snapshot: ArchSnapshot,
    config: ArchiLensConfig,
    output_dir: Optional[str | Path] = None,
) -> list[Path]:
    """Generate all diagrams from an architecture snapshot."""
    if output_dir is None:
        output_dir = config.diagrams.output_dir
    
    logger.info(f"Generating diagrams to {output_dir} (format: {config.diagrams.format})...")
    generator = get_generator(config)
    generated = generator.generate_all(snapshot, output_dir)
    logger.info(f"Generated {len(generated)} diagram files")
    
    return generated


def run_full_pipeline(
    repo_path: str | Path,
    use_ai: bool = True,
    output_dir: Optional[str | Path] = None,
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

def _analyze_with_progress(files, modules, repo_path):
    """Run dependency analysis with a rich progress bar for large repos."""
    from archilens.analyzers.dependencies import analyze_dependencies

    # Only show the progress bar for repos with enough files to matter
    if len(files) < 100:
        return analyze_dependencies(files, modules, repo_path)

    try:
        from rich.progress import Progress, SpinnerColumn, BarColumn, TaskProgressColumn, TextColumn
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[detail]}"),
            transient=True,
        ) as progress:
            task = progress.add_task(
                "Analyzing dependencies...", total=len(files), detail=""
            )

            def _on_file(relative_path: str) -> None:
                progress.update(task, advance=1, detail=relative_path[-40:])

            return analyze_dependencies(files, modules, repo_path, progress_cb=_on_file)
    except ImportError:
        return analyze_dependencies(files, modules, repo_path)


def _apply_capability_mappings(
    snapshot: ArchSnapshot,
    config: ArchiLensConfig,
) -> None:
    """Map modules to business capabilities based on config."""
    import fnmatch
    
    for cap in config.capabilities:
        matched_modules: list[str] = []
        for node in snapshot.get_module_nodes():
            module_path = node.file_path or node.id.replace("module:", "")
            for pattern in cap.modules:
                clean_pattern = pattern.replace("**", "*")
                if fnmatch.fnmatch(module_path, clean_pattern):
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

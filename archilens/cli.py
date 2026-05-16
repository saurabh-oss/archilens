"""
ArchiLens CLI — command-line interface for architecture visualization.

Usage:
    archilens analyze [--repo PATH] [--no-ai] [--output DIR]
    archilens diff --base REF --head REF [--repo PATH]
    archilens init [--repo PATH]
    archilens serve [--port PORT]
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

console = Console(highlight=False)

# Use only ASCII-safe symbols so Windows cp1252 consoles don't choke.
_OK = "[green]OK[/]"
_ARROW = "->"


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group()
@click.version_option(package_name="archilens")
def main() -> None:
    """ArchiLens — AI-powered architecture visualization for Git repositories."""
    pass


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to the Git repository")
@click.option("--output", "-o", default=None, help="Output directory for diagrams")
@click.option("--no-ai", is_flag=True, help="Disable AI-powered features")
@click.option("--level", "-l", type=int, default=None, help="Generate only this level (0-3)")
@click.option("--module", "-m", default=None, help="Drill into a specific module")
@click.option("--format", "-f", "fmt", default="mermaid", help="Output format: mermaid|d2|plantuml")
@click.option("--verbose", "-v", is_flag=True)
@click.option("--json-output", is_flag=True, help="Output snapshot as JSON")
def analyze(
    repo: str,
    output: str | None,
    no_ai: bool,
    level: int | None,
    module: str | None,
    fmt: str,
    verbose: bool,
    json_output: bool,
) -> None:
    """Analyze a repository and generate architecture diagrams."""
    _setup_logging(verbose)

    from archilens.config import ConfigError, load_config
    from archilens.engine import analyze_repository, generate_diagrams

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)

    try:
        config = load_config(repo_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)

    # Override config with CLI options
    if fmt != "mermaid":
        config.diagrams.format = fmt

    if level is not None:
        for lc in config.diagrams.levels:
            lc["enabled"] = lc["level"] == level

    with console.status("[bold blue]Analyzing repository..."):
        snapshot = analyze_repository(repo_path, config, use_ai=not no_ai)

    if json_output:
        console.print_json(snapshot.model_dump_json(indent=2))
        return

    # Display summary
    _print_summary(snapshot)

    # Generate diagrams
    out_dir = output or str(repo_path / config.diagrams.output_dir)
    diagrams = generate_diagrams(snapshot, config, out_dir)

    console.print()
    console.print(f"{_OK} Generated {len(diagrams)} diagrams in [bold]{out_dir}[/]")
    for d in diagrams:
        console.print(f"  {_ARROW} {d.name}")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to the Git repository")
@click.option("--base", required=True, help="Base Git ref (e.g., main, v1.0)")
@click.option("--head", required=True, help="Head Git ref (e.g., feature-branch, HEAD)")
@click.option("--output", "-o", default=None, help="Write diff markdown to this file")
@click.option("--verbose", "-v", is_flag=True)
def diff(repo: str, base: str, head: str, output: str | None, verbose: bool) -> None:
    """Compare architecture between two Git refs (drift detection)."""
    _setup_logging(verbose)

    from archilens.analyzers.diff import check_rules, compute_diff, format_diff_as_markdown
    from archilens.analyzers.git_utils import checkout_ref, resolve_ref
    from archilens.config import ConfigError, load_config
    from archilens.engine import analyze_repository

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)

    try:
        config = load_config(repo_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)

    # Resolve SHAs so the user sees exactly what is being compared
    base_sha = resolve_ref(repo_path, base) or base
    head_sha = resolve_ref(repo_path, head) or head
    console.print(f"Comparing [bold]{base}[/] ({base_sha[:8]}) -> [bold]{head}[/] ({head_sha[:8]})")

    try:
        with console.status(f"[bold blue]Extracting {base}..."), checkout_ref(repo_path, base) as base_dir:
            base_snapshot = analyze_repository(base_dir, config, use_ai=False, git_ref=base)

        with console.status(f"[bold blue]Extracting {head}..."), checkout_ref(repo_path, head) as head_dir:
            head_snapshot = analyze_repository(head_dir, config, use_ai=False, git_ref=head)

    except RuntimeError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)

    arch_diff = compute_diff(base_snapshot, head_snapshot)
    violations = check_rules(arch_diff, head_snapshot, config.ci.rules)
    md_output = format_diff_as_markdown(arch_diff)

    console.print()
    console.print(md_output)

    if output:
        Path(output).write_text(md_output, encoding="utf-8")
        console.print(f"\n{_OK} Diff written to [bold]{output}[/]")

    if violations:
        console.print(f"\n[yellow]WARN: {len(violations)} rule violation(s) found[/]")
        if any(v.startswith("[FAIL]") for v in violations):
            sys.exit(1)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to initialize")
def init(repo: str) -> None:
    """Initialize a .archilens.yml config file with sensible defaults."""
    repo_path = Path(repo).resolve()
    config_path = repo_path / ".archilens.yml"

    if config_path.exists():
        console.print("[yellow]Config file already exists.[/] Use --force to overwrite.")
        return

    # Copy the example config
    example_config = Path(__file__).parent.parent / ".archilens.yml"
    if example_config.exists():
        import shutil

        shutil.copy(example_config, config_path)
    else:
        # Generate minimal config
        config_path.write_text(
            "# ArchiLens Configuration\n"
            "# See: https://github.com/your-org/archilens#configuration\n\n"
            "project:\n"
            f'  name: "{repo_path.name}"\n'
            '  type: "monolith"\n\n'
            "analysis:\n"
            "  exclude:\n"
            '    - "**/node_modules/**"\n'
            '    - "**/__pycache__/**"\n'
            '    - "**/venv/**"\n'
            '    - "**/.git/**"\n\n'
            "diagrams:\n"
            '  output_dir: ".archilens/diagrams"\n'
            '  format: "mermaid"\n\n'
            "ai:\n"
            '  provider: "anthropic"\n'
            '  model: "claude-sonnet-4-20250514"\n',
            encoding="utf-8",
        )

    console.print(f"{_OK} Created {config_path}")
    console.print("  Edit this file to customize analysis settings.")


# ---------------------------------------------------------------------------
# history (evolution timeline)
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to the Git repository")
@click.option("--refs", default=None, help="Comma-separated list of git refs to analyse")
@click.option("--tags", "use_tags", is_flag=True, help="Auto-discover semver tags")
@click.option("--max-refs", default=10, show_default=True, help="Maximum number of refs to analyse")
@click.option("--output", "-o", default=None, help="Output directory for reports")
@click.option("--verbose", "-v", is_flag=True)
def history(repo: str, refs: str | None, use_tags: bool, max_refs: int, output: str | None, verbose: bool) -> None:
    """Analyse architecture evolution across git history (tags / refs)."""
    _setup_logging(verbose)

    from archilens.analyzers.evolution import (
        analyze_evolution,
        generate_evolution_report,
        generate_timeline_diagram,
    )
    from archilens.config import ConfigError, load_config

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)

    try:
        config = load_config(repo_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)
    ref_list = [r.strip() for r in refs.split(",")] if refs else None

    with console.status("[bold blue]Analysing git history..."):
        timeline = analyze_evolution(
            repo_path,
            config,
            refs=ref_list,
            use_tags=use_tags,
            max_refs=max_refs,
        )

    if not timeline.refs:
        console.print("[yellow]No refs could be analysed. Make sure this is a Git repository with commits.[/]")
        sys.exit(1)

    # Print summary table
    from rich.table import Table

    table = Table(title=f"Architecture Evolution: {timeline.project_name}")
    table.add_column("Ref", style="cyan")
    table.add_column("Date")
    table.add_column("Commit")
    table.add_column("Modules", justify="right")
    table.add_column("LOC", justify="right")
    table.add_column("Nodes", justify="right")
    table.add_column("Edges", justify="right")
    table.add_column("Patterns")

    for snap in timeline.refs:
        table.add_row(
            snap.ref,
            snap.timestamp,
            snap.commit_sha,
            str(snap.module_count),
            f"{snap.total_loc:,}",
            str(snap.node_count),
            str(snap.edge_count),
            ", ".join(snap.patterns) or "-",
        )
    console.print(table)

    if timeline.additions:
        console.print()
        console.print("[bold]New modules:[/]")
        for mod, ref in timeline.additions:
            console.print(f"  [green]+[/] {mod} (at {ref})")
    if timeline.removals:
        console.print()
        console.print("[bold]Removed modules:[/]")
        for mod, ref in timeline.removals:
            console.print(f"  [red]-[/] {mod} (at {ref})")

    # Write output files
    out_dir = Path(output) if output else repo_path / config.diagrams.output_dir / "evolution"
    out_dir.mkdir(parents=True, exist_ok=True)

    timeline_md = out_dir / "TIMELINE.md"
    timeline_md.write_text(generate_timeline_diagram(timeline), encoding="utf-8")

    report_md = out_dir / "EVOLUTION_REPORT.md"
    report_md.write_text(generate_evolution_report(timeline), encoding="utf-8")

    console.print()
    console.print(f"{_OK} Reports written to [bold]{out_dir}[/]")
    console.print(f"  {_ARROW} {timeline_md.name}")
    console.print(f"  {_ARROW} {report_md.name}")


# ---------------------------------------------------------------------------
# serve (interactive viewer)
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to the Git repository")
@click.option("--port", "-p", default=8765, show_default=True, help="Port for the viewer server")
@click.option("--ai", "use_ai", is_flag=True, help="Enable AI-powered enrichment (requires API key)")
@click.option("--debug", is_flag=True, help="Run Flask in debug mode")
def serve(repo: str, port: int, use_ai: bool, debug: bool) -> None:
    """Launch the interactive architecture viewer (local web UI)."""
    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)

    try:
        from archilens.viewer.app import create_app
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)

    console.print("[bold blue]ArchiLens Viewer[/]")
    console.print(f"  Repository : [cyan]{repo_path}[/]")
    console.print(f"  AI features: {'[green]enabled[/]' if use_ai else '[dim]disabled (use --ai to enable)[/]'}")
    console.print(f"  URL        : [bold]http://localhost:{port}[/]")
    console.print("[dim]Press Ctrl+C to stop[/]")
    console.print()
    console.print(
        "[yellow]NOTE:[/] This uses Flask's built-in dev server — "
        "intended for local use only. Do not expose to the public internet."
    )
    console.print()

    app = create_app(repo_path, use_ai=use_ai)
    app.run(host="0.0.0.0", port=port, debug=debug)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_summary(snapshot) -> None:
    """Print a rich summary of the analysis results."""
    console.print()
    console.print(f"[bold blue]ArchiLens Analysis[/]: {snapshot.project_name}")
    console.print(f"[dim]Ref: {snapshot.git_ref} | {snapshot.timestamp}[/]")
    console.print()

    # Module table
    module_nodes = snapshot.get_module_nodes()
    if module_nodes:
        table = Table(title="Modules Discovered")
        table.add_column("Module", style="cyan")
        table.add_column("LOC", justify="right")
        table.add_column("Dependencies", justify="right")
        table.add_column("Capability", style="green")
        table.add_column("Summary")

        for node in sorted(module_nodes, key=lambda n: n.lines_of_code, reverse=True):
            deps = sum(1 for e in snapshot.edges if e.source == node.id)
            table.add_row(
                node.name,
                f"{node.lines_of_code:,}",
                str(deps),
                node.capability or "-",
                (node.ai_summary or "")[:50],
            )

        console.print(table)

    # Patterns
    if snapshot.detected_patterns:
        console.print()
        console.print("[bold]Detected Patterns:[/]", end=" ")
        console.print(", ".join(p.value.replace("_", " ").title() for p in snapshot.detected_patterns))

    # Flows
    if snapshot.flows:
        console.print()
        console.print(f"[bold]Process Flows:[/] {len(snapshot.flows)}")
        for flow in snapshot.flows[:5]:
            console.print(f"  * {flow.name} ({len(flow.steps)} steps): {flow.trigger}")


if __name__ == "__main__":
    main()

"""
ArchiLens CLI — command-line interface for architecture visualization.

Usage:
    archilens analyze [--repo PATH] [--no-ai] [--output DIR]
    archilens diff --base REF --head REF [--repo PATH]
    archilens check [--repo PATH]
    archilens init [--repo PATH]
    archilens serve [--port PORT]
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

if TYPE_CHECKING:
    from archilens.models import ArchSnapshot

console = Console(highlight=False)

# Logs and progress go to stderr so stdout stays clean when it is redirected
# (`archilens diff ... > diff.md`, `archilens analyze --json-output > snapshot.json`).
err_console = Console(stderr=True, highlight=False)

# Use only ASCII-safe symbols so Windows cp1252 consoles don't choke.
_OK = "[green]OK[/]"
_ARROW = "->"


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, rich_tracebacks=True)],
    )


@click.group()
@click.version_option(package_name="archilens")
def main() -> None:
    """ArchiLens — AI-powered architecture visualization for Git repositories."""
    _tolerant_streams()


def _tolerant_streams() -> None:
    """
    Show "?" instead of crashing when the console can't encode a character. A legacy
    Windows code page (cp1252), e.g. with output redirected to a file, can't encode
    Rich's progress spinner, and the whole analysis died with UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


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

    with err_console.status("[bold blue]Analyzing repository..."):
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
@click.option(
    "--ai",
    "use_ai",
    is_flag=True,
    help="Add an AI review: impact summary, new-module descriptions, flows of new entry points",
)
@click.option("--verbose", "-v", is_flag=True)
def diff(repo: str, base: str, head: str, output: str | None, use_ai: bool, verbose: bool) -> None:
    """Compare architecture between two Git refs (drift detection)."""
    _setup_logging(verbose)

    from archilens.analyzers.diff import check_rules, compute_diff
    from archilens.analyzers.git_utils import changed_files, checkout_ref, resolve_ref
    from archilens.config import ConfigError, load_config
    from archilens.engine import analyze_repository, review_diff
    from archilens.generators.diff_report import format_diff_as_markdown

    # stdout carries only the markdown report (it gets redirected into PR
    # comments), so every status and error message in this command uses stderr.
    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        err_console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)

    try:
        config = load_config(repo_path)
    except ConfigError as exc:
        err_console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)

    # Resolve SHAs so the user sees exactly what is being compared
    base_sha = resolve_ref(repo_path, base) or base
    head_sha = resolve_ref(repo_path, head) or head
    err_console.print(f"Comparing [bold]{base}[/] ({base_sha[:8]}) -> [bold]{head}[/] ({head_sha[:8]})")

    try:
        with err_console.status(f"[bold blue]Extracting {base}..."), checkout_ref(repo_path, base) as base_dir:
            base_snapshot = analyze_repository(base_dir, config, use_ai=False, git_ref=base)

        with checkout_ref(repo_path, head) as head_dir:
            with err_console.status(f"[bold blue]Extracting {head}..."):
                head_snapshot = analyze_repository(head_dir, config, use_ai=False, git_ref=head)

            arch_diff = compute_diff(base_snapshot, head_snapshot)
            # Violations already on the base ref are reported as "existing" and, with the
            # default ci.fail_on: new, don't fail the build.
            check_rules(arch_diff, head_snapshot, config.ci.rules, base=base_snapshot)

            if use_ai:
                # The head tree stays on disk for the review: outlines and flows read its code.
                with err_console.status("[bold blue]Writing the AI review..."):
                    try:
                        skipped = review_diff(
                            arch_diff, head_snapshot, head_dir, config, changed_files(repo_path, base, head)
                        )
                    except Exception as exc:  # the static report must still be written
                        skipped = f"unexpected error ({exc})"
                if skipped:
                    err_console.print(f"[yellow]WARN: No AI review: {skipped}.[/] The report is the static diff.")

    except RuntimeError as exc:
        err_console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)

    md_output = format_diff_as_markdown(arch_diff)

    # Plain echo: Rich would hard-wrap table rows at the terminal width.
    click.echo(md_output)

    if output:
        Path(output).write_text(md_output, encoding="utf-8")
        err_console.print(f"\n{_OK} Diff written to [bold]{output}[/]")

    new, existing = arch_diff.violations, arch_diff.existing_violations
    if new or existing:
        err_console.print(f"\n[yellow]WARN: {len(new)} new rule violation(s), {len(existing)} already on {base}[/]")
    failing = new + (existing if config.ci.fail_on == "all" else [])
    if any(v.action == "fail" for v in failing):
        sys.exit(1)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to the repository")
@click.option("--json-output", is_flag=True, help="Print violations as JSON")
@click.option("--verbose", "-v", is_flag=True)
def check(repo: str, json_output: bool, verbose: bool) -> None:
    """Check the working tree against the rules in .archilens.yml (exit 1 on a 'fail' rule)."""
    _setup_logging(verbose)

    from archilens.config import ConfigError, load_config
    from archilens.engine import analyze_repository
    from archilens.rules import evaluate_rules

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        err_console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)
    try:
        config = load_config(repo_path)
    except ConfigError as exc:
        err_console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)

    if not config.ci.rules:
        err_console.print("No rules configured: add ci.rules to .archilens.yml.")
        if json_output:
            click.echo("[]")
        return

    with err_console.status("[bold blue]Analyzing repository..."):
        snapshot = analyze_repository(repo_path, config, use_ai=False)
    violations = evaluate_rules(snapshot, config.ci.rules)

    if json_output:
        click.echo(json.dumps([v.model_dump() for v in violations], indent=2))
    elif violations:
        for violation in violations:
            click.echo(f"- {violation.text()}")
    else:
        click.echo(f"No rule violations ({len(config.ci.rules)} rule(s) checked).")

    if any(v.action == "fail" for v in violations):
        sys.exit(1)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
#: Root folders `init` excludes in the config it writes, with the reason shown there.
#: (Example apps, docs and benchmarks are excluded by default for everyone; see AnalysisConfig.)
_LIKELY_NOT_PRODUCT = {
    "integration": "looks like integration tests",
    "scripts": "looks like developer scripts",
    "tools": "looks like developer tooling",
    "fixtures": "looks like test fixtures",
    "playground": "looks like a playground",
}


@main.command()
@click.option("--repo", "-r", default=".", help="Path to initialize")
def init(repo: str) -> None:
    """Write a .archilens.yml for this repository, with its languages and modules filled in."""
    from collections import Counter

    from archilens.analyzers.dependencies import analyze_dependencies, unanalyzed_languages
    from archilens.analyzers.discovery import LANGUAGE_NAMES, discover_files
    from archilens.analyzers.external import detect_external_systems
    from archilens.analyzers.layout import ROOT_MODULE
    from archilens.config import ArchiLensConfig
    from archilens.models import ArchSnapshot

    repo_path = Path(repo).resolve()
    config_path = repo_path / ".archilens.yml"

    if config_path.exists():
        console.print(f"[yellow]Config file already exists:[/] {config_path} (left unchanged).")
        return

    # A quick static discovery (no AI) so the examples use this repository's real modules.
    defaults = ArchiLensConfig()
    files, discovered_modules = discover_files(repo_path, defaults)
    sources = [f for f in files if not f.is_test]
    # Root folders that usually hold tooling or test apps rather than product code: excluded in
    # the written config, visibly, since whether they are product code depends on the repository.
    top = Counter(f.relative_path.split("/", 1)[0] for f in sources if "/" in f.relative_path)
    extra = {d: reason for d, reason in _LIKELY_NOT_PRODUCT.items() if top[d]}
    sources = [f for f in sources if f.relative_path.split("/", 1)[0] not in extra]
    languages = Counter(f.language for f in sources)
    module_files = Counter(f.module for f in sources)
    modules = [m for m, _ in module_files.most_common() if m != ROOT_MODULE]
    module_a, module_b = (modules + ["src/orders", "src/payments"])[:2]  # placeholders for an empty repo
    listed = [f"#   - {m} ({module_files[m]} file{'s' * (module_files[m] != 1)})" for m in modules[:15]]
    if len(modules) > 15:
        listed.append(f"#   ... and {len(modules) - 15} more")

    # External systems the code already talks to, read from its third-party imports.
    _, _, graph = analyze_dependencies(sources, discovered_modules, repo_path)
    systems = detect_external_systems(graph.graph.get("unresolved_imports", []))

    # The annotated template ships inside the package, so `init` works from a pip install.
    template = (Path(__file__).parent / "templates" / "archilens.example.yml").read_text(encoding="utf-8")
    values = {
        "project_name": repo_path.name.replace('"', ""),
        "languages": ", ".join(lang for lang, _ in languages.most_common()) or "none yet",
        "module_list": "\n".join(listed) or "#   (none found yet)",
        "module_a": module_a,
        "module_b": module_b,
        "module_a_slug": module_a.replace("/", "-"),
        "module_b_slug": module_b.replace("/", "-"),
        "detected_systems": (
            "Detected in this repository: " + ", ".join(s.name for s in systems) + "."
            if systems
            else "None detected in this repository yet."
        ),
        "exclude": "\n".join(
            [f'    - "{pattern}"' for pattern in defaults.analysis.exclude]
            + [f'    - "{d}/**"  # {reason}; delete this line to analyse it' for d, reason in extra.items()]
        ),
    }
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    config_path.write_text(template, encoding="utf-8")

    console.print(f"{_OK} Created {config_path}")
    if languages:
        counts = ", ".join(f"{n} {LANGUAGE_NAMES.get(lang, lang)}" for lang, n in languages.most_common())
        plural = "s" * (len(sources) != 1), "s" * (len(module_files) != 1)
        console.print(f"  Found {counts} file{plural[0]} in {len(module_files)} module{plural[1]}.")
    else:
        console.print("  Found no source files yet; the examples in it use placeholder module paths.")
    if extra:
        console.print(f"  Left out {', '.join(f'{d}/' for d in extra)} (see analysis.exclude to keep any of them).")
    if systems:
        console.print(f"  Detected external systems: {', '.join(s.name for s in systems)}.")
    note = ArchSnapshot(project_name="", unanalyzed=unanalyzed_languages(sources)).unanalyzed_note()
    if note:
        console.print(f"  [yellow]{note}.[/]")
    console.print("  Next: name your capabilities and rules in it, then run `archilens analyze`.")


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
    table.add_column("Components", justify="right")
    table.add_column("Dependencies", justify="right")
    table.add_column("Patterns")

    for snap in timeline.refs:
        table.add_row(
            snap.ref,
            snap.timestamp,
            snap.commit_sha,
            str(snap.module_count),
            f"{snap.total_loc:,}",
            str(snap.node_count - snap.module_count),
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
    if timeline.dependency_additions or timeline.dependency_removals:
        console.print()
        console.print(
            f"[bold]Dependency changes:[/] {len(timeline.dependency_additions)} added, "
            f"{len(timeline.dependency_removals)} removed (listed in EVOLUTION_REPORT.md)"
        )

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
# serve-mcp (MCP server for AI agents)
# ---------------------------------------------------------------------------
@main.command("serve-mcp")
@click.option("--repo", "-r", default=".", help="Path to the Git repository")
@click.option(
    "--transport",
    "-t",
    default="stdio",
    type=click.Choice(["stdio", "sse"]),
    show_default=True,
    help="MCP transport: stdio (Claude Code / Cursor) or sse (HTTP endpoint)",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface for SSE transport")
@click.option("--port", "-p", default=8766, show_default=True, help="Port for SSE transport")
@click.option("--refresh", is_flag=True, help="Force re-analysis, ignore snapshot cache")
@click.option("--verbose", "-v", is_flag=True)
def serve_mcp(repo: str, transport: str, host: str, port: int, refresh: bool, verbose: bool) -> None:
    """Launch the ArchiLens MCP server for AI coding agents (Claude Code, Cursor, etc.)."""
    _setup_logging(verbose)

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        # For stdio transport stderr is safe; for sse stdout is fine too
        click.echo(f"Repository path does not exist: {repo_path}", err=True)
        sys.exit(1)

    try:
        import mcp.server.fastmcp  # noqa: F401  (the server module only imports it when building the server)

        from archilens.mcp.server import create_mcp_server, init_server
    except ImportError:
        click.echo("The MCP server needs the 'mcp' extra: pip install \"archilens[mcp]\"", err=True)
        sys.exit(1)

    if refresh:
        from archilens.mcp.cache import invalidate_cache

        invalidate_cache(repo_path)
        click.echo(f"Cache invalidated for {repo_path.name}", err=True)

    init_server(repo_path)

    if transport == "sse":
        console.print("[bold blue]ArchiLens MCP Server[/] (SSE transport)")
        console.print(f"  Repository : [cyan]{repo_path}[/]")
        console.print(f"  Endpoint   : [bold]http://{host}:{port}/sse[/]")
        console.print()
        console.print("Add to your MCP client config:")
        console.print(f'  {{"url": "http://{host}:{port}/sse"}}')
        console.print()
    else:
        # stdio: print setup instructions to stderr only — stdout is the MCP wire protocol
        click.echo(f"ArchiLens MCP Server (stdio) — repo: {repo_path}", err=True)
        click.echo(
            "Register it with Claude Code:\n"
            f'  claude mcp add archilens -- archilens serve-mcp --repo "{repo_path}"\n'
            "Other MCP clients (e.g. Cursor): add a server with\n"
            f'  {{"command": "archilens", "args": ["serve-mcp", "--repo", "{repo_path.as_posix()}"]}}',
            err=True,
        )

    # FastMCP takes host/port at construction time, not in run().
    mcp_server = create_mcp_server(host=host, port=port)
    if transport == "sse":
        mcp_server.run(transport="sse")
    else:
        mcp_server.run(transport="stdio")


# ---------------------------------------------------------------------------
# serve (interactive viewer)
# ---------------------------------------------------------------------------
@main.command()
@click.option("--repo", "-r", default=".", help="Path to the Git repository")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Interface to bind. Use 0.0.0.0 only on a trusted network: the viewer has no authentication.",
)
@click.option("--port", "-p", default=8765, show_default=True, help="Port for the viewer server")
@click.option("--ai", "use_ai", is_flag=True, help="Enable AI-powered enrichment (requires API key)")
@click.option("--debug", is_flag=True, help="Run Flask in debug mode (loopback --host only)")
def serve(repo: str, host: str, port: int, use_ai: bool, debug: bool) -> None:
    """Launch the interactive architecture viewer (local web UI)."""
    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[red]Repository path does not exist:[/] {repo_path}")
        sys.exit(1)

    try:
        import flask  # noqa: F401  (create_app only imports it when called)

        from archilens.viewer.app import create_app
    except ImportError:
        # "\\[" keeps Rich from reading [viewer] as a markup tag
        console.print("[red]The viewer needs the 'viewer' extra:[/] pip install \"archilens\\[viewer]\"")
        sys.exit(1)

    # Flask's debug mode serves the Werkzeug debugger, which can execute arbitrary code.
    if debug and host not in ("127.0.0.1", "localhost", "::1"):
        console.print("[red]--debug is only allowed with a loopback --host (127.0.0.1).[/]")
        sys.exit(1)

    console.print("[bold blue]ArchiLens Viewer[/]")
    console.print(f"  Repository : [cyan]{repo_path}[/]")
    console.print(f"  AI features: {'[green]enabled[/]' if use_ai else '[dim]disabled (use --ai to enable)[/]'}")
    console.print(f"  URL        : [bold]http://{host}:{port}[/]")
    console.print("[dim]Press Ctrl+C to stop[/]")
    console.print()
    console.print(
        "[yellow]NOTE:[/] This uses Flask's built-in dev server — "
        "intended for local use only. Do not expose to the public internet."
    )
    console.print()

    app = create_app(repo_path, use_ai=use_ai)
    app.run(host=host, port=port, debug=debug)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_summary(snapshot: ArchSnapshot) -> None:
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
                node.capability
                or (f"[dim]{node.suggested_capability} (suggested)[/]" if node.suggested_capability else "-"),
                (node.ai_summary or "")[:50],
            )

        console.print(table)

    # External systems
    if snapshot.detected_systems:
        console.print()
        console.print("[bold]External systems (detected from imports):[/]", end=" ")
        console.print(", ".join(s.name for s in snapshot.detected_systems))

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

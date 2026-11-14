"""
ArchiLens MCP server — exposes architecture knowledge to AI coding agents.

Four tools:
  get_module_context       — What owns this file? What are its dependencies?
  check_dependency_allowed — Is this import permitted by CI rules?
  find_capability_owner    — Which module/capability handles this feature?
  get_architecture_context — Compact architecture text for AI context windows.

Run via CLI:
  archilens serve-mcp --repo /path/to/repo              # stdio (Claude Code, Cursor)
  archilens serve-mcp --repo /path/to/repo --transport sse --port 8766  # HTTP/SSE

Add to Claude Code settings:
  {
    "mcpServers": {
      "archilens": {
        "command": "archilens",
        "args": ["serve-mcp", "--repo", "/path/to/repo"]
      }
    }
  }
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from archilens.models import EdgeType

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from archilens.config import ArchiLensConfig, CIRule
    from archilens.models import ArchNode, ArchSnapshot

logger = logging.getLogger(__name__)

# Module-level state populated by init_server() before the server starts.
_repo_path: Path | None = None
_snapshot: ArchSnapshot | None = None
_config: ArchiLensConfig | None = None
_loaded_key: str | None = None  # _state_key() the current _snapshot was built for


def init_server(repo_path: Path) -> None:
    """Set the repository path.  Must be called before create_mcp_server()."""
    global _repo_path
    _repo_path = repo_path.resolve()


def _git(repo_path: Path, *args: str) -> bytes | None:
    """Run a read-only git command in *repo_path*; None if git is unavailable or fails."""
    try:
        proc = subprocess.run(["git", "-C", str(repo_path), *args], capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _current_head(repo_path: Path) -> str:
    """Return the short HEAD SHA, or 'HEAD' if git is unavailable."""
    out = _git(repo_path, "rev-parse", "HEAD")
    return out.decode().strip()[:12] if out else "HEAD"


def _worktree_fingerprint(repo_path: Path) -> str:
    """
    Fingerprint uncommitted changes: "" for a clean tree, else a hash of which files
    changed plus their size and mtime, so further edits to an already-dirty file
    still change it.
    """
    status = _git(repo_path, "status", "--porcelain", "-z", "--untracked-files=normal")
    if not status:
        return ""
    digest = hashlib.sha1(status)
    for entry in status.split(b"\0"):
        rel = entry[3:].decode("utf-8", errors="surrogateescape")
        if rel:
            with contextlib.suppress(OSError):
                st = (repo_path / rel).stat()
                digest.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}".encode())
    return digest.hexdigest()[:12]


def _archilens_version() -> str:
    """
    Installed ArchiLens version, from package metadata. (Importing ``archilens.__version__``
    would make this package depend on the top-level CLI module, which depends on it.)
    """
    try:
        return importlib.metadata.version("archilens")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _state_key(repo_path: Path) -> tuple[str, bool]:
    """
    Identify everything the snapshot depends on: HEAD, uncommitted edits, the
    .archilens.yml contents and the ArchiLens version (an upgrade can change the
    analysis for the same commit). Returns (key, worktree_is_clean).
    """
    config_hash = hashlib.sha1()
    for name in (".archilens.yml", ".archilens.yaml"):
        with contextlib.suppress(OSError):
            config_hash.update((repo_path / name).read_bytes())
    dirty = _worktree_fingerprint(repo_path)
    key = f"{_current_head(repo_path)}|v{_archilens_version()}|cfg:{config_hash.hexdigest()[:12]}"
    return (f"{key}|wt:{dirty}" if dirty else key), not dirty


def _ensure_loaded() -> ArchSnapshot:
    """
    Lazily load (or restore from cache) the ArchSnapshot and return it.

    Called at the start of every tool invocation. The snapshot is rebuilt whenever
    the repository state changes (new commit, uncommitted edits, config change, or
    an ArchiLens upgrade), so an agent editing files never gets stale answers.
    """
    global _snapshot, _config, _loaded_key
    if _repo_path is None:
        raise RuntimeError(
            "ArchiLens MCP server not initialised — repo_path is not set. Use 'archilens serve-mcp --repo PATH'."
        )

    key, clean = _state_key(_repo_path)
    if _snapshot is not None and key == _loaded_key:
        return _snapshot

    from archilens.config import load_config
    from archilens.mcp.cache import load_snapshot, save_snapshot

    _config = load_config(_repo_path)

    # Only clean trees go to the on-disk cache; dirty states are transient.
    cached = load_snapshot(_repo_path, key) if clean else None
    if cached is not None:
        logger.info("Restored snapshot from cache (%s @ %s)", _repo_path.name, key)
        _snapshot, _loaded_key = cached, key
        return _snapshot

    logger.info("Analysing %s (%s) ...", _repo_path, key)
    from archilens.engine import analyze_repository

    _snapshot = analyze_repository(_repo_path, _config, use_ai=False, git_ref=_current_head(_repo_path))
    _loaded_key = key
    if clean:
        save_snapshot(_repo_path, key, _snapshot)
    logger.info("Analysis complete.")
    return _snapshot


def _module_for_path(snap: ArchSnapshot, file_path: str) -> ArchNode | None:
    """
    Find the module node that owns *file_path*: the module whose directory is the
    longest prefix of the file's directory. Modules are directories (named by
    their path), so this matches how discovery assigned the file, whatever the
    source root or module depth.
    """
    module = _module_path_for(file_path, [n.id.replace("module:", "") for n in snap.get_module_nodes()])
    return next((n for n in snap.get_module_nodes() if n.id == f"module:{module}"), None) if module else None


def _repo_relative(file_path: str) -> str:
    """Normalise *file_path* (relative or absolute) to a repository-relative POSIX path."""
    norm = file_path.replace("\\", "/")
    # ValueError: already relative, or not under the repository root
    if _repo_path is not None:
        with contextlib.suppress(ValueError):
            norm = Path(file_path).resolve().relative_to(_repo_path).as_posix()
    return "/".join(p for p in norm.split("/") if p and p != ".")


def _module_path_for(file_path: str, module_paths: list[str]) -> str | None:
    """Longest module path that contains *file_path*; ``<root>`` for top-level files."""
    rel = _repo_relative(file_path)
    directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
    best: str | None = None
    for module in module_paths:
        if module == "<root>":
            if directory == "" and best is None:
                best = module
        elif (directory == module or directory.startswith(module + "/")) and (
            best in (None, "<root>") or len(module) > len(best or "")
        ):
            best = module
    return best


def proposed_dependency_verdict(
    snap: ArchSnapshot, rules: list[CIRule], source_module: str, target: str
) -> dict[str, Any] | None:
    """
    Would a new dependency from *source_module* to *target* break a rule?

    Checks forbidden-dependency rules (paths and capabilities), whether the edge
    would close a dependency cycle (no-cycles), and whether it would push the
    source over a max-fan-out threshold. Returns the most severe matching rule
    as a tool result, or None if no rule objects.
    """
    import networkx as nx

    from archilens.rules import forbids

    modules = {n.id.removeprefix("module:"): n for n in snap.get_module_nodes()}
    target_module = target if target in modules else (_module_path_for(f"{target}/_", list(modules)) or target)
    capability = {path: node.capability for path, node in modules.items()}
    deps: dict[str, set[str]] = {}
    for edge in snap.edges:
        if edge.edge_type == EdgeType.DEPENDENCY and edge.source.startswith("module:"):
            deps.setdefault(edge.source.removeprefix("module:"), set()).add(edge.target.removeprefix("module:"))

    matches: list[tuple[CIRule, str]] = []
    for rule in rules:
        if rule.kind == "forbidden-dependency" and forbids(
            rule, source_module, target_module, capability.get(source_module), capability.get(target_module)
        ):
            matches.append((rule, f"dependency from '{source_module}' to '{target_module}'"))
        elif rule.kind == "no-cycles" and source_module != target_module:
            graph = nx.DiGraph([(src, tgt) for src, tgts in deps.items() for tgt in tgts])
            if (
                graph.has_node(target_module)
                and graph.has_node(source_module)
                and nx.has_path(graph, target_module, source_module)
            ):
                matches.append((rule, f"dependency from '{source_module}' to '{target_module}', which closes a cycle"))
        elif rule.kind == "max-fan-out" and rule.threshold:
            fan_out = len(deps.get(source_module, set()) | {target_module})
            if fan_out > rule.threshold:
                matches.append(
                    (rule, f"'{source_module}' depending on {fan_out} modules (threshold: {rule.threshold})")
                )

    if not matches:
        return None
    rule, what = max(matches, key=lambda m: m[0].action == "fail")
    blocked = rule.action == "fail"
    return {
        "allowed": not blocked,
        "reason": f"Rule '{rule.name}' ({rule.kind}) {'forbids' if blocked else 'warns about'} {what}.",
        "rule": rule.name,
        "action": rule.action,
    }


# ---------------------------------------------------------------------------
# FastMCP server factory
# ---------------------------------------------------------------------------


def create_mcp_server(host: str = "127.0.0.1", port: int = 8766) -> FastMCP:
    """
    Build and return a configured FastMCP server instance.

    The server is stateless at creation time; all tool handlers call
    _ensure_loaded() to lazily populate the snapshot on first use.

    Args:
        host: Interface the SSE transport binds to (ignored for stdio).
        port: Port the SSE transport listens on (ignored for stdio).
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "ArchiLens",
        instructions=(
            "Architecture knowledge graph for the repository. "
            "Call get_architecture_context('L1') first to understand module structure. "
            "Call get_module_context(file_path) before editing any file. "
            "Call check_dependency_allowed(source, target) before adding any import."
        ),
        host=host,
        port=port,
    )

    # ------------------------------------------------------------------
    # Tool 1: get_module_context
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_module_context(file_path: str) -> dict[str, Any]:
        """
        Return the module owning this file, its business capability, AI summary,
        lines of code, and top direct dependencies / reverse dependencies.

        Call this before editing any file to understand its architectural role.

        Args:
            file_path: Relative path from repo root (e.g. "src/orders/service.py")
                       or an absolute filesystem path.
        """
        snap = _ensure_loaded()

        node = _module_for_path(snap, file_path)
        if node is None:
            known = [n.name for n in snap.get_module_nodes()]
            return {
                "error": f"No module found for '{file_path}'.",
                "known_modules": known,
                "tip": "Pass a path relative to the repo root, e.g. 'agents/crew.py'.",
            }

        deps = [e.target.replace("module:", "") for e in snap.edges if e.source == node.id][:5]

        rdeps = [e.source.replace("module:", "") for e in snap.edges if e.target == node.id][:5]

        return {
            "module": node.name,
            "module_id": node.id,
            "capability": node.capability or "Uncategorised",
            # AI suggestion for modules with no configured capability; not used by rules
            "suggested_capability": node.suggested_capability,
            "summary": node.ai_summary or "No AI summary — run with --ai to generate.",
            "lines_of_code": node.lines_of_code,
            "component_count": len(snap.get_children(node.id)),
            "top_dependencies": deps,
            "depended_on_by": rdeps,
        }

    # ------------------------------------------------------------------
    # Tool 2: check_dependency_allowed
    # ------------------------------------------------------------------

    @mcp.tool()
    def check_dependency_allowed(source_file: str, target_module: str) -> dict[str, Any]:
        """
        Check whether an import from source_file to target_module is permitted
        by the CI rules defined in .archilens.yml.

        Returns {allowed, reason, rule, action}.

        Call this before adding any import to avoid architecture rule violations.

        Args:
            source_file:   Path of the file that will contain the new import.
            target_module: Module name or path being imported (e.g. "infrastructure/db").
        """
        snap = _ensure_loaded()

        # Derive the source module the same way get_module_context does; for a new
        # file with no module yet, fall back to its directory.
        module_paths = [n.id.replace("module:", "") for n in snap.get_module_nodes()]
        rel = _repo_relative(source_file)
        source_module = _module_path_for(source_file, module_paths) or (rel.rsplit("/", 1)[0] if "/" in rel else rel)

        if _config is None or not _config.ci.rules:
            return {
                "allowed": True,
                "reason": "No CI rules configured in .archilens.yml.",
                "rule": None,
                "action": None,
            }

        verdict = proposed_dependency_verdict(snap, _config.ci.rules, source_module, target_module)
        if verdict is not None:
            return verdict

        return {
            "allowed": True,
            "reason": "No rules restrict this dependency — it is permitted.",
            "rule": None,
            "action": None,
        }

    # ------------------------------------------------------------------
    # Tool 3: find_capability_owner
    # ------------------------------------------------------------------

    @mcp.tool()
    def find_capability_owner(feature_description: str) -> dict[str, Any]:
        """
        Find which module and business capability best matches a feature description.

        Useful when adding a new feature and needing to know where it belongs
        architecturally, or when refactoring to understand ownership boundaries.

        Args:
            feature_description: Natural language description of the feature or
                                  concept (e.g. "user authentication and sessions",
                                  "payment processing and invoicing").
        """
        snap = _ensure_loaded()

        query_words = set(feature_description.lower().split())
        scored: list[tuple[float, str, str, str]] = []

        for node in snap.get_module_nodes():
            score = 0.0
            name_words = set(node.name.lower().replace("_", " ").replace("-", " ").split())
            cap_words = set((node.capability or node.suggested_capability or "").lower().replace("_", " ").split())
            summary_words = set((node.ai_summary or "").lower().split())

            score += len(query_words & name_words) * 3.0
            score += len(query_words & cap_words) * 2.0
            score += len(query_words & summary_words) * 1.0

            if score > 0:
                scored.append((score, node.id, node.name, node.capability or "Uncategorised"))

        scored.sort(reverse=True)

        if not scored:
            return {
                "match": None,
                "reason": "No module matched the feature description.",
                "all_capabilities": list(snap.capability_map.keys()),
                "tip": "Try using module or capability names from get_architecture_context().",
            }

        best_score, best_id, best_name, best_cap = scored[0]
        max_possible = len(query_words) * 3.0
        confidence = round(best_score / max_possible, 2) if max_possible > 0 else 0.0

        return {
            "match": {
                "module": best_name,
                "module_id": best_id,
                "capability": best_cap,
                "confidence": confidence,
            },
            "alternatives": [
                {"module": name, "capability": cap, "score": round(s, 1)} for s, _, name, cap in scored[1:4]
            ],
        }

    # ------------------------------------------------------------------
    # Tool 4: get_architecture_context
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_architecture_context(scope: str = "L1") -> str:
        """
        Return a compact text summary of the architecture, suitable for injecting
        into an AI agent's context window.

        Args:
            scope: One of:
                "L0"          — External systems and actors
                "L1"          — Module map with capabilities and key edges (default)
                "L2:<module>" — Class-level detail for a module (e.g. "L2:agents")
                "capabilities"— Business capability groupings only
        """
        snap = _ensure_loaded()
        lines: list[str] = []

        if scope == "L0":
            lines.append(f"# {snap.project_name} — System Context (L0)")
            lines.append("")
            if _config and _config.external_systems:
                lines.append("## External Systems")
                for ext in _config.external_systems:
                    lines.append(f"  - {ext.name} ({ext.type}): {ext.description}")
            if snap.detected_systems:
                lines.append("## External Systems (detected from imports)")
                for system in snap.detected_systems:
                    lines.append(
                        f"  - {system.name} ({system.type}): {system.used_by()}; "
                        f"imported as {', '.join(system.packages)}"
                    )
            if not (_config and _config.external_systems) and not snap.detected_systems:
                lines.append("No external systems configured or detected.")
            if snap.detected_patterns:
                lines.append("")
                lines.append("## Detected Patterns")
                for p in snap.detected_patterns:
                    lines.append(f"  - {p.value.replace('_', ' ').title()}")

        elif scope.startswith("L2:"):
            target = scope[3:].strip().lower()
            module_node = next(
                (
                    n
                    for n in snap.get_module_nodes()
                    if n.name.lower() == target or target in n.id.lower().replace("module:", "")
                ),
                None,
            )
            if module_node is None:
                known = [n.name for n in snap.get_module_nodes()]
                return f"Module '{target}' not found. Known modules: {known}"

            children = snap.get_children(module_node.id)
            lines.append(f"# {module_node.name} — Component Detail (L2)")
            lines.append(f"Capability : {module_node.capability or 'Uncategorised'}")
            lines.append(f"LOC        : {module_node.lines_of_code:,}")
            lines.append(f"Components : {len(children)}")
            if module_node.ai_summary:
                lines.append(f"Summary    : {module_node.ai_summary}")
            lines.append("")
            lines.append("## Classes / Components")
            for child in children[:40]:
                lines.append(f"  - {child.name} ({child.node_type.value})")
            if len(children) > 40:
                lines.append(f"  ... and {len(children) - 40} more")

        elif scope == "capabilities":
            lines.append(f"# {snap.project_name} — Business Capabilities")
            lines.append("")
            if not snap.capability_map:
                lines.append("No capability mappings defined in .archilens.yml.")
            for cap_name, mod_ids in snap.capability_map.items():
                lines.append(f"## {cap_name}")
                for mod_id in mod_ids:
                    node = next((n for n in snap.nodes if n.id == mod_id), None)
                    if node:
                        summary = f": {node.ai_summary}" if node.ai_summary else ""
                        lines.append(f"  - {node.name}{summary}")
                lines.append("")

        else:  # L1 (default)
            modules = snap.get_module_nodes()
            lines.append(f"# {snap.project_name} — Module Architecture (L1)")
            lines.append(
                f"Summary: {len(modules)} modules, {len(snap.edges)} edges, "
                f"{sum(n.lines_of_code for n in modules):,} total LOC"
            )
            lines.append("")

            if snap.capability_map:
                lines.append("## Business Capabilities")
                for cap_name, mod_ids in snap.capability_map.items():
                    mod_names = [n.name for n in modules if n.id in mod_ids]
                    lines.append(f"  {cap_name}: {', '.join(mod_names)}")
                lines.append("")

            lines.append("## Modules (sorted by LOC)")
            for node in sorted(modules, key=lambda n: n.lines_of_code, reverse=True)[:25]:
                dep_count = sum(1 for e in snap.edges if e.source == node.id)
                cap = f" [{node.capability}]" if node.capability else ""
                lines.append(f"  {node.name}{cap} — {node.lines_of_code:,} LOC, {dep_count} outgoing deps")

            top_edges = sorted(snap.edges, key=lambda e: e.weight, reverse=True)[:12]
            if top_edges:
                lines.append("")
                lines.append("## Key Dependencies (by import frequency)")
                for edge in top_edges:
                    src = edge.source.replace("module:", "")
                    tgt = edge.target.replace("module:", "")
                    lines.append(f"  {src} -> {tgt}  ({edge.weight} imports)")

        return "\n".join(lines)

    return mcp

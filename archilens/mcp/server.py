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

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level state populated by init_server() before the server starts.
_repo_path: Path | None = None
_snapshot = None   # ArchSnapshot | None
_config = None     # ArchiLensConfig | None


def init_server(repo_path: Path) -> None:
    """Set the repository path.  Must be called before create_mcp_server()."""
    global _repo_path
    _repo_path = repo_path.resolve()


def _current_head(repo_path: Path) -> str:
    """Return the short HEAD SHA, or 'HEAD' if git is unavailable."""
    try:
        import git  # type: ignore[import]
        repo = git.Repo(repo_path, search_parent_directories=True)
        return repo.head.commit.hexsha[:12]
    except Exception:
        return "HEAD"


def _ensure_loaded() -> None:
    """
    Lazily load (or restore from cache) the ArchSnapshot.
    Called at the start of every tool invocation.
    """
    global _snapshot, _config
    if _snapshot is not None:
        return
    if _repo_path is None:
        raise RuntimeError(
            "ArchiLens MCP server not initialised — repo_path is not set. "
            "Use 'archilens serve-mcp --repo PATH'."
        )

    from archilens.config import load_config
    from archilens.mcp.cache import load_snapshot, save_snapshot

    _config = load_config(_repo_path)
    git_ref = _current_head(_repo_path)

    cached = load_snapshot(_repo_path, git_ref)
    if cached is not None:
        logger.info("Restored snapshot from cache (%s @ %s)", _repo_path.name, git_ref)
        _snapshot = cached
        return

    logger.info("No cache hit — running analysis on %s ...", _repo_path)
    from archilens.engine import analyze_repository

    _snapshot = analyze_repository(_repo_path, _config, use_ai=False, git_ref=git_ref)
    save_snapshot(_repo_path, git_ref, _snapshot)
    logger.info("Analysis complete — snapshot cached.")


def _module_for_path(file_path: str):  # -> ArchNode | None
    """
    Find the module node that owns *file_path*.

    Strategy: normalise the path, strip it to the first 1–2 components, and
    match against module IDs (which are stored as the module path without the
    'module:' prefix).
    """
    snap = _snapshot
    norm = file_path.replace("\\", "/")

    # If an absolute path was passed, try to make it relative to the repo root
    if _repo_path is not None:
        try:
            norm = str(Path(file_path).resolve().relative_to(_repo_path)).replace("\\", "/")
        except ValueError:
            pass  # already relative or not under repo root

    parts = [p for p in norm.split("/") if p and p != "."]

    candidates = [
        "/".join(parts[:2]),  # depth-2: "src/orders"
        parts[0] if parts else "",  # depth-1: "src"
    ]

    for node in snap.get_module_nodes():
        mod_path = node.id.replace("module:", "")
        if mod_path in candidates:
            return node

    return None


# ---------------------------------------------------------------------------
# FastMCP server factory
# ---------------------------------------------------------------------------


def create_mcp_server():  # -> FastMCP
    """
    Build and return a configured FastMCP server instance.

    The server is stateless at creation time; all tool handlers call
    _ensure_loaded() to lazily populate the snapshot on first use.
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore[import]

    mcp = FastMCP(
        "ArchiLens",
        instructions=(
            "Architecture knowledge graph for the repository. "
            "Call get_architecture_context('L1') first to understand module structure. "
            "Call get_module_context(file_path) before editing any file. "
            "Call check_dependency_allowed(source, target) before adding any import."
        ),
    )

    # ------------------------------------------------------------------
    # Tool 1: get_module_context
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_module_context(file_path: str) -> dict:
        """
        Return the module owning this file, its business capability, AI summary,
        lines of code, and top direct dependencies / reverse dependencies.

        Call this before editing any file to understand its architectural role.

        Args:
            file_path: Relative path from repo root (e.g. "src/orders/service.py")
                       or an absolute filesystem path.
        """
        _ensure_loaded()
        snap = _snapshot

        node = _module_for_path(file_path)
        if node is None:
            known = [n.name for n in snap.get_module_nodes()]
            return {
                "error": f"No module found for '{file_path}'.",
                "known_modules": known,
                "tip": "Pass a path relative to the repo root, e.g. 'agents/crew.py'.",
            }

        deps = [
            e.target.replace("module:", "")
            for e in snap.edges
            if e.source == node.id
        ][:5]

        rdeps = [
            e.source.replace("module:", "")
            for e in snap.edges
            if e.target == node.id
        ][:5]

        return {
            "module": node.name,
            "module_id": node.id,
            "capability": node.capability or "Uncategorised",
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
    def check_dependency_allowed(source_file: str, target_module: str) -> dict:
        """
        Check whether an import from source_file to target_module is permitted
        by the CI rules defined in .archilens.yml.

        Returns {allowed, reason, rule, action}.

        Call this before adding any import to avoid architecture rule violations.

        Args:
            source_file:   Path of the file that will contain the new import.
            target_module: Module name or path being imported (e.g. "infrastructure/db").
        """
        _ensure_loaded()
        import fnmatch

        # Derive source module from file path
        norm = source_file.replace("\\", "/")
        if _repo_path is not None:
            try:
                norm = str(Path(source_file).resolve().relative_to(_repo_path)).replace("\\", "/")
            except ValueError:
                pass
        parts = [p for p in norm.split("/") if p and p != "."]
        source_module = "/".join(parts[:min(2, len(parts))]) if parts else source_file

        if _config is None or not _config.ci.rules:
            return {
                "allowed": True,
                "reason": "No CI rules configured in .archilens.yml.",
                "rule": None,
                "action": None,
            }

        for rule in _config.ci.rules:
            if rule.name == "no-layer-skip" and rule.from_pattern and rule.to_pattern:
                fp = rule.from_pattern.replace("**", "*")
                tp = rule.to_pattern.replace("**", "*")
                if fnmatch.fnmatch(source_module, fp) and fnmatch.fnmatch(target_module, tp):
                    blocked = rule.action == "fail"
                    return {
                        "allowed": not blocked,
                        "reason": (
                            f"Rule '{rule.name}' {'forbids' if blocked else 'warns about'} "
                            f"dependency from '{source_module}' to '{target_module}'."
                        ),
                        "rule": rule.name,
                        "action": rule.action,
                    }

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
    def find_capability_owner(feature_description: str) -> dict:
        """
        Find which module and business capability best matches a feature description.

        Useful when adding a new feature and needing to know where it belongs
        architecturally, or when refactoring to understand ownership boundaries.

        Args:
            feature_description: Natural language description of the feature or
                                  concept (e.g. "user authentication and sessions",
                                  "payment processing and invoicing").
        """
        _ensure_loaded()
        snap = _snapshot

        query_words = set(feature_description.lower().split())
        scored: list[tuple[float, str, str, str]] = []

        for node in snap.get_module_nodes():
            score = 0.0
            name_words = set(node.name.lower().replace("_", " ").replace("-", " ").split())
            cap_words = set((node.capability or "").lower().replace("_", " ").split())
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
                {"module": name, "capability": cap, "score": round(s, 1)}
                for s, _, name, cap in scored[1:4]
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
        _ensure_loaded()
        snap = _snapshot
        lines: list[str] = []

        if scope == "L0":
            lines.append(f"# {snap.project_name} — System Context (L0)")
            lines.append("")
            if _config and _config.external_systems:
                lines.append("## External Systems")
                for ext in _config.external_systems:
                    lines.append(f"  - {ext.name} ({ext.type}): {ext.description}")
            else:
                lines.append("No external systems configured.")
            if snap.detected_patterns:
                lines.append("")
                lines.append("## Detected Patterns")
                for p in snap.detected_patterns:
                    lines.append(f"  - {p.value.replace('_', ' ').title()}")

        elif scope.startswith("L2:"):
            target = scope[3:].strip().lower()
            module_node = next(
                (
                    n for n in snap.get_module_nodes()
                    if n.name.lower() == target
                    or target in n.id.lower().replace("module:", "")
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
                lines.append(
                    f"  {node.name}{cap} — {node.lines_of_code:,} LOC, {dep_count} outgoing deps"
                )

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

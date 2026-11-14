"""
Abstract base for diagram generators.

Each format (Mermaid, D2, PlantUML) implements this interface so that
engine.py can dispatch by ``config.diagrams.format`` without knowing the
details of any specific syntax.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from pathlib import Path

from archilens.config import ArchiLensConfig
from archilens.models import ArchEdge, ArchNode, ArchSnapshot, NodeType, ProcessFlow


class DiagramGenerator(ABC):
    """Base class for all diagram format generators."""

    #: File extension for generated files (without leading dot)
    extension: str = "md"

    def __init__(self, config: ArchiLensConfig) -> None:
        self.config = config

    def generate_all(
        self,
        snapshot: ArchSnapshot,
        output_dir: str | Path,
    ) -> list[Path]:
        """
        Generate all configured diagram levels and write to *output_dir*.

        Returns a list of created file paths.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        generated: list[Path] = []
        descriptions: dict[str, str] = {}  # file name -> one line for the INDEX.md table
        used: set[str] = set()  # file stems already written, so same-named modules don't overwrite

        for level_cfg in self.config.diagrams.levels:
            if not level_cfg.get("enabled", True):
                continue

            level = level_cfg["level"]

            if level == 0:
                path = output_dir / f"L0_system_context.{self.extension}"
                path.write_text(self.system_context(snapshot), encoding="utf-8")
                generated.append(path)
                descriptions[path.name] = "The system, its users and the external systems it talks to"

            elif level == 1:
                path = output_dir / f"L1_module_architecture.{self.extension}"
                path.write_text(self.module_architecture(snapshot), encoding="utf-8")
                generated.append(path)
                descriptions[path.name] = "Modules, grouped by business capability, and the dependencies between them"

            elif level == 2:
                modules = level_cfg.get("modules", [])
                module_nodes = snapshot.get_module_nodes()
                targets = (
                    [n for n in module_nodes if any(n.file_path and n.file_path.startswith(m) for m in modules)]
                    if modules
                    else module_nodes
                )
                for mod_node in targets:
                    stem = _unique(_slug(mod_node.name), _slug(mod_node.file_path or mod_node.id), used)
                    path = output_dir / f"L2_{stem}_components.{self.extension}"
                    content = self.component_detail(snapshot, mod_node.id)
                    if content:
                        path.write_text(content, encoding="utf-8")
                        generated.append(path)
                        descriptions[path.name] = mod_node.ai_summary or f"Classes and functions in {mod_node.name}"

            elif level == 3:
                max_flows = level_cfg.get("max_flows", 10)
                for flow in snapshot.flows[:max_flows]:
                    stem = _unique(_slug(flow.name), _slug(flow.id), used)
                    path = output_dir / f"L3_{stem}.{self.extension}"
                    path.write_text(self.process_flow(flow), encoding="utf-8")
                    generated.append(path)
                    descriptions[path.name] = flow.description or flow.trigger

        index_path = output_dir / "INDEX.md"
        index_path.write_text(self.index(snapshot, generated, descriptions), encoding="utf-8")
        generated.append(index_path)

        return generated

    @abstractmethod
    def system_context(self, snapshot: ArchSnapshot) -> str:
        """Generate L0 system context diagram."""

    @abstractmethod
    def module_architecture(self, snapshot: ArchSnapshot) -> str:
        """Generate L1 module architecture diagram."""

    @abstractmethod
    def component_detail(self, snapshot: ArchSnapshot, module_id: str) -> str | None:
        """Generate L2 component detail diagram for a module."""

    @abstractmethod
    def process_flow(self, flow: ProcessFlow) -> str:
        """Generate L3 process flow / sequence diagram."""

    def index(
        self,
        snapshot: ArchSnapshot,
        generated: list[Path],
        descriptions: dict[str, str] | None = None,
    ) -> str:
        """Generate an INDEX.md linking all diagrams (format-agnostic)."""
        descriptions = descriptions or {}
        lines = [
            f"# ArchiLens — {snapshot.project_name}",
            "",
            f"*Generated from `{snapshot.git_ref or 'HEAD'}`*",
            f"*Format: {self.__class__.__name__.replace('Generator', '')}*",
            "",
            "## Architecture Diagrams",
            "",
            "| Level | Diagram | Description |",
            "|-------|---------|-------------|",
        ]
        for f in generated:
            if f.name == "INDEX.md":
                continue
            name = f.stem.replace("_", " ")
            level = name[:2] if name[:2] in ("L0", "L1", "L2", "L3") else "--"
            description = shorten(descriptions.get(f.name, ""), 160).replace("|", "\\|")
            lines.append(f"| {level} | [{name}]({f.name}) | {description} |")

        lines.append("")
        if snapshot.capability_map:
            lines.append("## Business Capabilities")
            lines.append("")
            for cap_name, mod_ids in snapshot.capability_map.items():
                lines.append(f"### {cap_name}")
                for mod_id in mod_ids:
                    node = next((n for n in snapshot.nodes if n.id == mod_id), None)
                    if node:
                        lines.append(f"- {node.name}")
                lines.append("")

        return "\n".join(lines)


#: Most module dependencies drawn on L1 by default; beyond this the diagram is a tangle
#: (Django has 109). ``diagrams.l1_max_edges`` overrides it, and 0 draws all of them.
L1_MAX_EDGES = 40


def select_l1_edges(edges: list[ArchEdge], limit: int = L1_MAX_EDGES) -> list[ArchEdge]:
    """
    The dependencies L1 draws: all of them up to *limit*; beyond that, the heaviest
    (most imports), after first giving every module its heaviest dependency so no
    module looks unconnected when it isn't. Returned in a stable (source, target) order.
    """
    heaviest_first = sorted(edges, key=lambda e: (-e.weight, e.source, e.target))
    if limit <= 0 or len(edges) <= limit:
        chosen = heaviest_first
    else:
        chosen, covered = [], set()
        for edge in heaviest_first:  # the spine: each module's heaviest dependency
            if edge.source not in covered or edge.target not in covered:
                chosen.append(edge)
                covered |= {edge.source, edge.target}
        spine = {(e.source, e.target, e.edge_type) for e in chosen}
        for edge in heaviest_first:
            if len(chosen) >= limit:
                break
            if (edge.source, edge.target, edge.edge_type) not in spine:
                chosen.append(edge)
    return sorted(chosen, key=lambda e: (e.source, e.target))


def stroke_width(weight: int, heaviest: int) -> float:
    """
    Line width for a dependency of *weight* imports, when the heaviest one drawn has
    *heaviest*: 1px for a single import up to 4px for the heaviest, on a log scale so
    the differences stay visible whether the heaviest is 5 imports or 500. Rounded to
    half pixels, which keeps the number of distinct styles small.
    """
    if heaviest <= 1 or weight <= 1:
        return 1.0
    share = math.log(min(weight, heaviest)) / math.log(heaviest)
    return round((1.0 + 3.0 * share) * 2) / 2


def omitted_note(shown: int, total: int) -> str:
    """'' when everything is drawn, else what the reader is not seeing."""
    if shown >= total:
        return ""
    return (
        f"Showing {shown} of {total} dependencies: the heaviest, and at least one for every module. "
        "Set diagrams.l1_max_edges: 0 to draw all"
    )


#: Most components drawn on one L2 diagram by default. Past this nobody can read it, and a
#: few hundred boxes exceed what Mermaid will render at all (Django's ``db`` has 600).
#: ``diagrams.l2_max_components`` overrides it, and 0 draws all of them.
L2_MAX_COMPONENTS = 60

#: Most functions listed in one component's box.
MAX_MEMBERS = 8


def select_l2(
    snapshot: ArchSnapshot, module_id: str, limit: int = L2_MAX_COMPONENTS
) -> tuple[list[ArchNode], list[ArchEdge], list[ArchNode]]:
    """
    What one module's L2 diagram draws: (components, relationships between them, every
    component of the module). All of them up to *limit*; beyond that, the ones with the
    most relationships inside the module, kept in their original order.
    """
    everything = snapshot.get_children(module_id)
    ids = {c.id for c in everything}
    edges = [e for e in snapshot.edges if e.source in ids and e.target in ids]
    if limit <= 0 or len(everything) <= limit:
        return everything, edges, everything
    degree = dict.fromkeys(ids, 0)
    for edge in edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    position = {c.id: i for i, c in enumerate(everything)}
    kept = {c.id for c in sorted(everything, key=lambda c: (-degree[c.id], position[c.id]))[:limit]}
    return (
        [c for c in everything if c.id in kept],
        [e for e in edges if e.source in kept and e.target in kept],
        everything,
    )


def l2_note(shown: int, total: int) -> str:
    """'' when every component is drawn, else what the reader is not seeing."""
    if shown >= total:
        return ""
    return (
        f"Showing {shown} of {total} components: the ones with the most relationships inside the module. "
        "Set diagrams.l2_max_components: 0 to draw all"
    )


def member_names(node: ArchNode) -> tuple[list[str], str]:
    """
    The functions to list in a component's box, and a line saying so when that is not all of
    them ("12 functions, 8 listed"; '' otherwise). One function over the limit is just listed.
    """
    total = len(node.members)
    if total <= MAX_MEMBERS + 1:
        return node.members, ""
    return node.members[:MAX_MEMBERS], f"{total} functions, {MAX_MEMBERS} listed"


def component_kind(node: ArchNode) -> str:
    """ "class", "interface", "component", or "functions" for a file of functions."""
    return "functions" if node.node_type == NodeType.FUNCTION else node.node_type.value


def l0_systems(snapshot: ArchSnapshot, config: ArchiLensConfig) -> list[tuple[str, str, str, str]]:
    """
    The external systems L0 draws, as (name, type, description, source): those declared in the
    config, then those detected from imports (which never repeat a declared one).
    """
    systems = [(e.name, e.type, e.description, "declared in .archilens.yml") for e in config.external_systems]
    for d in snapshot.detected_systems:
        systems.append((d.name, d.type, d.used_by(), "detected from imports of " + ", ".join(d.packages)))
    return systems


#: External-system types drawn as a cylinder.
STORES = ("database", "cache", "storage")


def shorten(text: str, width: int) -> str:
    """*text* on one line, cut at a word boundary to at most *width* characters, ending in "…" if cut."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    cut = text[: width - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:.-") + "…"


def _slug(text: str) -> str:
    """
    A file-name stem that is valid on every OS and safe in a Markdown link:
    lower-case ASCII letters, digits, ``.``, ``-`` and ``_`` only.
    """
    return re.sub(r"[^a-z0-9._-]+", "_", text.lower()).strip("._") or "diagram"


def _unique(preferred: str, fallback: str, used: set[str]) -> str:
    """*preferred*, or *fallback* (then a counter) when another diagram already took it."""
    for candidate in (preferred, f"{preferred}_{fallback}"):
        if candidate not in used:
            used.add(candidate)
            return candidate
    n = 2
    while f"{preferred}_{n}" in used:
        n += 1
    used.add(f"{preferred}_{n}")
    return f"{preferred}_{n}"


def get_generator(config: ArchiLensConfig) -> DiagramGenerator:
    """Instantiate the correct generator for ``config.diagrams.format``."""
    fmt = (config.diagrams.format or "mermaid").lower()

    if fmt == "d2":
        from archilens.generators.d2 import D2Generator

        return D2Generator(config)
    elif fmt in ("plantuml", "puml"):
        from archilens.generators.plantuml import PlantUMLGenerator

        return PlantUMLGenerator(config)
    else:
        from archilens.generators.mermaid import MermaidGenerator

        return MermaidGenerator(config)

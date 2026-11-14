"""
D2 diagram generator (https://d2lang.com).

D2 is a modern diagram scripting language with a clean, readable syntax.
Unlike Mermaid it is not natively rendered on GitHub, but it produces
significantly nicer output via the ``d2`` CLI and is better for
large-scale architecture diagrams.

Install D2: https://d2lang.com/tour/install
Render:     d2 L1_module_architecture.d2 architecture.svg

Two D2 rules shape this generator: a connection must name a shape by its full
path (``container.child``), otherwise D2 silently creates a second, top-level
shape; and every label is emitted as a quoted string, because unquoted labels
end at characters such as ``:``, ``{`` or ``;``.
"""

from __future__ import annotations

import re

from archilens.generators.base import DiagramGenerator, omitted_note, select_l1_edges, shorten, stroke_width
from archilens.models import (
    ArchSnapshot,
    EdgeType,
    NodeType,
    ProcessFlow,
)


class D2Generator(DiagramGenerator):
    """Generates D2 diagram files."""

    extension = "d2"

    # -------------------------------------------------------------------
    # L0: System Context
    # -------------------------------------------------------------------

    def system_context(self, snapshot: ArchSnapshot) -> str:
        lines: list[str] = [
            f"# L0 System Context: {_comment(snapshot.project_name)}",
            "# Render with: d2 this_file.d2 output.svg",
            "",
        ]

        sys_id = "system"
        lines.append(f"{sys_id}: {_string(snapshot.project_name)} {{")
        lines.append("  shape: rectangle")
        lines.append('  style.fill: "#1168bd"')
        lines.append('  style.stroke: "#5aa0e6"')
        lines.append('  style.font-color: "#ffffff"')
        lines.append("}")
        lines.append("")

        for index, ext in enumerate(self.config.external_systems):
            ext_id = f"ext_{index}_{_id(ext.name)}"
            shape = "cylinder" if ext.type in ("database", "cache") else "rectangle"
            lines.append(f"{ext_id}: {_string(ext.name)} {{")
            lines.append(f"  shape: {shape}")
            lines.append('  style.fill: "#64748b"')
            lines.append('  style.stroke: "#94a3b8"')
            lines.append('  style.font-color: "#ffffff"')
            if ext.description:
                lines.append(f"  tooltip: {_tooltip(ext.description)}")
            lines.append("}")
            lines.append(f"{sys_id} -> {ext_id}")
            lines.append("")

        has_http = any(f.trigger.startswith(("GET", "POST", "PUT", "DELETE", "PATCH")) for f in snapshot.flows)
        if has_http:
            lines.append('user: "User / Client" {')
            lines.append("  shape: person")
            lines.append("}")
            lines.append(f"user -> {sys_id}")
            lines.append("")

        if snapshot.detected_patterns:
            lines.append("# Detected patterns:")
            for p in snapshot.detected_patterns:
                lines.append(f"#   {p.value.replace('_', ' ').title()}")

        return "\n".join(lines)

    # -------------------------------------------------------------------
    # L1: Module Architecture
    # -------------------------------------------------------------------

    def module_architecture(self, snapshot: ArchSnapshot) -> str:
        module_nodes = snapshot.get_module_nodes()
        if not module_nodes:
            return "# L1: No modules detected\n"

        node_ids = {n.id for n in module_nodes}
        edges = snapshot.get_edges_for_nodes(node_ids)

        lines: list[str] = [
            f"# L1 Module Architecture: {_comment(snapshot.project_name)}",
            "direction: right",
            "",
        ]

        # Full D2 path of every module shape, so connections reach the shape
        # inside its capability container instead of creating a duplicate.
        paths: dict[str, str] = {}

        for index, (cap_name, mod_ids) in enumerate(snapshot.capability_map.items()):
            cap_id = f"cap_{index}_{_id(cap_name)}"
            members = [n for n in module_nodes if n.id in mod_ids and n.id not in paths]
            if not members:
                continue
            lines.append(f"{cap_id}: {_string(cap_name)} {{")
            for node in members:
                nid = _id(node.id)
                paths[node.id] = f"{cap_id}.{nid}"
                lines += _module_shape(
                    nid, node.name, node.lines_of_code, node.ai_summary, "#1168bd", indent="  ", stroke="#5aa0e6"
                )
            lines.append("}")
            lines.append("")

        for node in module_nodes:
            if node.id not in paths:
                nid = _id(node.id)
                paths[node.id] = nid
                lines += _module_shape(
                    nid, node.name, node.lines_of_code, node.ai_summary, "#2a74c4", indent="", stroke="#7cb4ec"
                )

        lines.append("")

        # Line width shows the import count; at most l1_max_edges dependencies are drawn.
        shown = select_l1_edges(edges, self.config.diagrams.l1_max_edges)
        heaviest = max((e.weight for e in shown), default=1)
        note = omitted_note(len(shown), len(edges))
        if note:
            lines.append(f"# {note}")
        if snapshot.unanalyzed:
            lines.append(f"# {snapshot.unanalyzed_note()}")
        for edge in shown:
            # D2 only accepts whole numbers here ("expected stroke-width to be a number between 0 and 15").
            width = max(1, round(stroke_width(edge.weight, heaviest)))
            lines.append(f"{paths[edge.source]} -> {paths[edge.target]}: {{style.stroke-width: {width}}}")

        return "\n".join(lines)

    # -------------------------------------------------------------------
    # L2: Component Detail
    # -------------------------------------------------------------------

    def component_detail(self, snapshot: ArchSnapshot, module_id: str) -> str | None:
        children = snapshot.get_children(module_id)
        if not children:
            return None

        module_node = next((n for n in snapshot.nodes if n.id == module_id), None)
        if not module_node:
            return None

        child_ids = {c.id for c in children}
        edges = [e for e in snapshot.edges if e.source in child_ids and e.target in child_ids]

        container = "module"
        lines: list[str] = [
            f"# L2 Component Detail: {_comment(module_node.name)}",
            "",
            f"{container}: {_string(module_node.name)} {{",
        ]

        paths: dict[str, str] = {}
        for index, child in enumerate(children):
            cid = f"c{index}_{_id(child.name)}"
            paths[child.id] = f"{container}.{cid}"
            interface = child.node_type == NodeType.INTERFACE
            label = f"«interface» {child.name}" if interface else child.name
            if child.file_path:
                label += f"\n{child.file_path}"
            lines.append(f"  {cid}: {_string(label)} {{")
            if interface:
                lines.append("    style.stroke-dash: 3")
            lines.append("  }")

        lines.append("}")
        lines.append("")

        for edge in edges:
            src, tgt = paths[edge.source], paths[edge.target]
            if edge.edge_type == EdgeType.INHERITANCE:
                lines.append(f'{src} -> {tgt}: "extends"')
            elif edge.edge_type == EdgeType.IMPLEMENTATION:
                lines.append(f'{src} -> {tgt}: "implements" {{style.stroke-dash: 3}}')
            else:
                lines.append(f"{src} -> {tgt}: {_string(edge.label or 'uses')}")

        return "\n".join(lines)

    # -------------------------------------------------------------------
    # L3: Process Flow (Sequence)
    # -------------------------------------------------------------------

    def process_flow(self, flow: ProcessFlow) -> str:
        lines: list[str] = [
            f"# L3 Process Flow: {_comment(flow.name)}",
        ]
        if flow.trigger:
            lines.append(f"# Trigger: {_comment(flow.trigger)}")
        if flow.description:
            lines.append(f"# {_comment(flow.description)}")
        lines.append("")
        lines.append("shape: sequence_diagram")
        lines.append("")

        # Declaring actors first fixes their order and lets groups refer to them.
        actor_ids: dict[str, str] = {}
        for step in flow.steps:
            for actor in (step.actor, step.target):
                if actor not in actor_ids:
                    actor_ids[actor] = f"a{len(actor_ids)}_{_id(actor)}"
                    lines.append(f"{actor_ids[actor]}: {_string(actor)}")

        lines.append("")

        for index, step in enumerate(flow.steps):
            label = step.action + (f" [{step.data}]" if step.data else "")
            style = " {style.stroke-dash: 3}" if step.is_async else ""
            message = f"{actor_ids[step.actor]} -> {actor_ids[step.target]}: {_string(label)}{style}"
            if step.condition:
                # A group (D2's equivalent of a UML "alt" fragment) around the guarded step.
                lines.append(f"g{index}: {_string('if ' + step.condition)} {{")
                lines.append(f"  {message}")
                lines.append("}")
            else:
                lines.append(message)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_shape(nid: str, name: str, loc: int, summary: str | None, fill: str, indent: str, stroke: str) -> list[str]:
    label = name + (f"\n{loc:,} LOC" if loc else "")
    lines = [
        f"{indent}{nid}: {_string(label)} {{",
        f'{indent}  style.fill: "{fill}"',
        f'{indent}  style.stroke: "{stroke}"',
        f'{indent}  style.font-color: "#ffffff"',
    ]
    if summary:
        lines.append(f"{indent}  tooltip: {_tooltip(shorten(summary, 120))}")
    lines.append(f"{indent}}}")
    return lines


def _id(raw: str) -> str:
    """A D2 key: letters, digits and underscores only (dots would mean nesting)."""
    key = re.sub(r"\W+", "_", raw, flags=re.ASCII).strip("_") or "node"
    return f"n_{key}" if key[0].isdigit() else key


def _string(text: str) -> str:
    """Render text as a D2 double-quoted string; newlines become ``\\n`` line breaks."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + "\\n".join(" ".join(part.split()) for part in escaped.split("\n")) + '"'


def _tooltip(text: str) -> str:
    """
    Tooltips are parsed as Markdown with embedded HTML: ``<state>`` is a compile
    error, and neither backslash escapes nor entities display correctly. Angle
    brackets become the look-alike ``‹ ›``; everything else is kept as written.
    """
    flat = " ".join(text.split())
    return _string(flat.replace("<", "‹").replace(">", "›"))


def _comment(text: str) -> str:
    """Text for a ``#`` comment line."""
    return " ".join(text.split())

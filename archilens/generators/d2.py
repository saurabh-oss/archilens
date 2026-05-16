"""
D2 diagram generator (https://d2lang.com).

D2 is a modern diagram scripting language with a clean, readable syntax.
Unlike Mermaid it is not natively rendered on GitHub, but it produces
significantly nicer output via the ``d2`` CLI and is better for
large-scale architecture diagrams.

Install D2: https://d2lang.com/tour/install
Render:     d2 L1_module_architecture.d2 architecture.svg
"""

from __future__ import annotations

from archilens.generators.base import DiagramGenerator
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
            f"# L0 System Context: {snapshot.project_name}",
            "# Render with: d2 this_file.d2 output.svg",
            "",
        ]

        # Central system
        sys_id = _id(snapshot.project_name)
        lines.append(f"{sys_id}: {snapshot.project_name} {{")
        lines.append("  shape: rectangle")
        lines.append('  style.fill: "#1168bd"')
        lines.append('  style.font-color: "#ffffff"')
        lines.append("}")
        lines.append("")

        # External systems
        for ext in self.config.external_systems:
            ext_id = _id(ext.name)
            shape = "cylinder" if ext.type in ("database", "cache") else "rectangle"
            lines.append(f"{ext_id}: {ext.name} {{")
            lines.append(f"  shape: {shape}")
            lines.append('  style.fill: "#999999"')
            lines.append('  style.font-color: "#ffffff"')
            if ext.description:
                lines.append(f'  tooltip: "{ext.description}"')
            lines.append("}")
            lines.append(f"{sys_id} -> {ext_id}")
            lines.append("")

        # User actor if flows suggest HTTP
        has_http = any(f.trigger.startswith(("GET", "POST", "PUT", "DELETE", "PATCH")) for f in snapshot.flows)
        if has_http:
            lines.append("user: User / Client {")
            lines.append("  shape: person")
            lines.append("}")
            lines.append(f"user -> {sys_id}")
            lines.append("")

        # Detected patterns
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
            f"# L1 Module Architecture: {snapshot.project_name}",
            "direction: right",
            "",
        ]

        # Capability groups as D2 containers
        grouped: set[str] = set()
        for cap_name, mod_ids in snapshot.capability_map.items():
            cap_id = _id(cap_name)
            lines.append(f"{cap_id}: {cap_name} {{")
            for mod_id in mod_ids:
                node = next((n for n in module_nodes if n.id == mod_id), None)
                if node:
                    nid = _id(node.id)
                    label = node.name
                    if node.lines_of_code:
                        label += f"\\n{node.lines_of_code:,} LOC"
                    lines.append(f"  {nid}: {_quote(label)} {{")
                    lines.append('    style.fill: "#1168bd"')
                    lines.append('    style.font-color: "#ffffff"')
                    if node.ai_summary:
                        lines.append(f'    tooltip: "{node.ai_summary[:120]}"')
                    lines.append("  }")
                    grouped.add(mod_id)
            lines.append("}")
            lines.append("")

        # Ungrouped modules
        for node in module_nodes:
            if node.id not in grouped:
                nid = _id(node.id)
                label = node.name
                if node.lines_of_code:
                    label += f"\\n{node.lines_of_code:,} LOC"
                lines.append(f"{nid}: {_quote(label)} {{")
                lines.append('  style.fill: "#438dd5"')
                lines.append('  style.font-color: "#ffffff"')
                if node.ai_summary:
                    lines.append(f'  tooltip: "{node.ai_summary[:120]}"')
                lines.append("}")

        lines.append("")

        # Edges
        for edge in edges:
            src = _id(edge.source)
            tgt = _id(edge.target)
            label = edge.label or ""
            if label:
                lines.append(f"{src} -> {tgt}: {label}")
            else:
                lines.append(f"{src} -> {tgt}")

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
        edges = [e for e in snapshot.edges if e.source in child_ids or e.target in child_ids]

        lines: list[str] = [
            f"# L2 Component Detail: {module_node.name}",
            "",
            f"{_id(module_id)}: {module_node.name} {{",
        ]

        for child in children:
            cid = _id(child.id)
            label = child.name
            if child.file_path:
                label += f"\\n{child.file_path}"
            node_shape = "class" if child.node_type == NodeType.CLASS else "rectangle"
            lines.append(f"  {cid}: {_quote(label)} {{")
            lines.append(f"    shape: {node_shape}")
            lines.append("  }")

        lines.append("}")
        lines.append("")

        for edge in edges:
            src_node = next((n for n in children if n.id == edge.source), None)
            tgt_node = next((n for n in children if n.id == edge.target), None)
            if src_node and tgt_node:
                src = _id(src_node.id)
                tgt = _id(tgt_node.id)
                if edge.edge_type == EdgeType.INHERITANCE:
                    lines.append(f"{src} -> {tgt}: extends")
                elif edge.edge_type == EdgeType.IMPLEMENTATION:
                    lines.append(f"{src} -> {tgt}: implements")
                else:
                    label = edge.label or "uses"
                    lines.append(f"{src} -> {tgt}: {label}")

        return "\n".join(lines)

    # -------------------------------------------------------------------
    # L3: Process Flow (Sequence)
    # -------------------------------------------------------------------

    def process_flow(self, flow: ProcessFlow) -> str:
        lines: list[str] = [
            f"# L3 Process Flow: {flow.name}",
        ]
        if flow.trigger:
            lines.append(f"# Trigger: {flow.trigger}")
        if flow.description:
            lines.append(f"# {flow.description}")
        lines.append("")
        lines.append("shape: sequence_diagram")
        lines.append("")

        # Declare actors
        seen: set[str] = set()
        for step in flow.steps:
            for actor in (step.actor, step.target):
                if actor not in seen:
                    seen.add(actor)
                    lines.append(f"{_id(actor)}: {actor}")

        lines.append("")

        for step in flow.steps:
            src = _id(step.actor)
            tgt = _id(step.target)
            label = step.action
            if step.data:
                label += f" [{step.data}]"
            arrow = "->" if step.is_async else "->"
            lines.append(f"{src}.{arrow} {tgt}: {label}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _id(raw: str) -> str:
    """Convert an arbitrary string to a valid D2 identifier."""
    return (
        raw.replace(":", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
        .replace(" ", "_")
        .replace("<", "")
        .replace(">", "")
        .strip("_")
    )


def _quote(text: str) -> str:
    """Wrap text in D2 double-quotes if it contains special chars."""
    if any(c in text for c in (" ", ":", "/", ".", "-")):
        return f'"{text}"'
    return text

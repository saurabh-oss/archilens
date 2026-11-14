"""
PlantUML diagram generator (https://plantuml.com).

PlantUML is widely supported across documentation platforms (Confluence,
GitLab, many IDE plugins). It produces component diagrams, class diagrams,
and sequence diagrams from a simple text DSL.

Render locally:  java -jar plantuml.jar *.puml
Online:          https://www.plantuml.com/plantuml/uml/
GitLab:          natively rendered in .puml files
"""

from __future__ import annotations

import re

from archilens.generators.base import DiagramGenerator, omitted_note, select_l1_edges, stroke_width
from archilens.models import (
    ArchSnapshot,
    EdgeType,
    NodeType,
    ProcessFlow,
)


class PlantUMLGenerator(DiagramGenerator):
    """Generates PlantUML (.puml) diagram files."""

    extension = "puml"

    # -------------------------------------------------------------------
    # L0: System Context
    # -------------------------------------------------------------------

    def system_context(self, snapshot: ArchSnapshot) -> str:
        lines: list[str] = [
            "@startuml L0_System_Context",
            "!theme cerulean",
            "",
            "title L0 System Context — " + _text(snapshot.project_name),
            "",
        ]

        # Skinparam
        lines += [
            "skinparam rectangle {",
            "  BackgroundColor #1168bd",
            "  FontColor #ffffff",
            "  BorderColor #5aa0e6",
            "}",
            "",
        ]

        sys_id = _safe_id(snapshot.project_name)
        lines.append(f'rectangle "{_text(snapshot.project_name)}" as {sys_id} #1168bd')
        lines.append("")

        for ext in self.config.external_systems:
            ext_id = _safe_id(ext.name)
            if ext.type in ("database", "cache"):
                lines.append(f'database "{_text(ext.name)}" as {ext_id} #64748b')
            else:
                lines.append(f'rectangle "{_text(ext.name)}" as {ext_id} #64748b')
            desc = ext.description
            if desc:
                lines.append(f"note right of {ext_id}: {_text(desc)}")
            lines.append(f"{sys_id} --> {ext_id}")
            lines.append("")

        has_http = any(f.trigger.startswith(("GET", "POST", "PUT", "DELETE", "PATCH")) for f in snapshot.flows)
        if has_http:
            lines.append('actor "User / Client" as user')
            lines.append(f"user --> {sys_id}")
            lines.append("")

        if snapshot.detected_patterns:
            lines.append(
                "note top of "
                + sys_id
                + " : Patterns:\\n"
                + "\\n".join(p.value.replace("_", " ").title() for p in snapshot.detected_patterns)
            )
            lines.append("")

        lines.append("@enduml")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # L1: Module Architecture
    # -------------------------------------------------------------------

    def module_architecture(self, snapshot: ArchSnapshot) -> str:
        module_nodes = snapshot.get_module_nodes()
        if not module_nodes:
            return "@startuml\nnote: No modules detected\n@enduml\n"

        node_ids = {n.id for n in module_nodes}
        edges = snapshot.get_edges_for_nodes(node_ids)

        lines: list[str] = [
            "@startuml L1_Module_Architecture",
            "!theme cerulean",
            "left to right direction",
            "",
            "title L1 Module Architecture — " + _text(snapshot.project_name),
            "",
        ]

        # Skinparam
        lines += [
            "skinparam component {",
            "  BackgroundColor #1168bd",
            "  FontColor #ffffff",
            "  BorderColor #5aa0e6",
            "}",
            "",
        ]

        # Group by capability using packages
        grouped: set[str] = set()
        for cap_name, mod_ids in snapshot.capability_map.items():
            lines.append(f'package "{_text(cap_name)}" {{')
            for mod_id in mod_ids:
                node = next((n for n in module_nodes if n.id == mod_id), None)
                if node:
                    nid = _safe_id(node.id)
                    label = node.name
                    if node.lines_of_code:
                        label += f"\\n{node.lines_of_code:,} LOC"
                    lines.append(f'  component "{_text(label)}" as {nid}')
                    grouped.add(mod_id)
            lines.append("}")
            lines.append("")

        # Ungrouped
        for node in module_nodes:
            if node.id not in grouped:
                nid = _safe_id(node.id)
                label = node.name
                if node.lines_of_code:
                    label += f"\\n{node.lines_of_code:,} LOC"
                lines.append(f'component "{_text(label)}" as {nid}')

        lines.append("")

        # Edges: thickness shows the import count; at most l1_max_edges are drawn.
        shown = select_l1_edges(edges, self.config.diagrams.l1_max_edges)
        heaviest = max((e.weight for e in shown), default=1)
        note = omitted_note(len(shown), len(edges))
        if note:
            lines.append(f"' {note}")
        if snapshot.unanalyzed:
            lines.append(f"' {snapshot.unanalyzed_note()}")
        for edge in shown:
            width = max(1, round(stroke_width(edge.weight, heaviest)))
            lines.append(f"{_safe_id(edge.source)} -[thickness={width}]-> {_safe_id(edge.target)}")

        lines.append("")

        # Module detail table as a note
        if module_nodes:
            lines.append("legend right")
            lines.append("  | Module | LOC | Deps |")
            for node in sorted(module_nodes, key=lambda n: n.lines_of_code, reverse=True)[:10]:
                dep_count = sum(1 for e in edges if e.source == node.id)
                lines.append(f"  | {node.name} | {node.lines_of_code:,} | {dep_count} |")
            lines.append("endlegend")

        lines.append("")
        lines.append("@enduml")
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
            f"@startuml L2_{_safe_id(module_node.name)}_Components",
            "!theme cerulean",
            "",
            f"title L2 Component Detail — {_text(module_node.name)}",
            "",
        ]

        for child in children:
            cid = _safe_id(child.id)
            if child.node_type == NodeType.INTERFACE:
                lines.append(f'interface "{_text(child.name)}" as {cid}')
            else:
                lines.append(f'class "{_text(child.name)}" as {cid}')
                if child.file_path:
                    lines.append(f"note bottom of {cid}: {child.file_path}")

        lines.append("")

        for edge in edges:
            src_n = next((n for n in children if n.id == edge.source), None)
            tgt_n = next((n for n in children if n.id == edge.target), None)
            if src_n and tgt_n:
                src = _safe_id(src_n.id)
                tgt = _safe_id(tgt_n.id)
                if edge.edge_type == EdgeType.INHERITANCE:
                    lines.append(f"{tgt} <|-- {src}")
                elif edge.edge_type == EdgeType.IMPLEMENTATION:
                    lines.append(f"{tgt} <|.. {src}")
                elif edge.edge_type == EdgeType.COMPOSITION:
                    lines.append(f"{src} *-- {tgt}")
                else:
                    label = edge.label or "uses"
                    lines.append(f"{src} --> {tgt} : {label}")

        lines.append("")
        lines.append("@enduml")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # L3: Process Flow (Sequence)
    # -------------------------------------------------------------------

    def process_flow(self, flow: ProcessFlow) -> str:
        safe_name = _safe_id(flow.name)
        lines: list[str] = [
            f"@startuml L3_{safe_name}",
            "!theme cerulean",
            "",
            f"title L3 Process Flow — {_text(flow.name)}",
        ]
        if flow.trigger:
            lines.append(f"' Trigger: {_text(flow.trigger)}")
        if flow.description:
            lines.append(f"' {_text(flow.description)}")
        lines.append("autonumber")
        lines.append("")

        # Participants (position-based IDs: "API Gateway" and "API-Gateway" stay distinct)
        participant: dict[str, str] = {}
        for step in flow.steps:
            for actor in (step.actor, step.target):
                if actor not in participant:
                    participant[actor] = f"p{len(participant)}"
                    lines.append(f'participant "{_text(actor)}" as {participant[actor]}')

        lines.append("")

        for step in flow.steps:
            src = participant[step.actor]
            tgt = participant[step.target]
            label = _text(step.action)
            if step.data:
                label += f"\\n[{_text(step.data)}]"

            if step.condition:
                lines.append(f"alt {_text(step.condition)}")

            if step.is_async:
                lines.append(f"{src} ->> {tgt} : {label}")
            else:
                lines.append(f"{src} -> {tgt} : {label}")

            if step.condition:
                lines.append("end")

        lines.append("")
        lines.append("@enduml")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_id(raw: str) -> str:
    """Convert arbitrary string to a PlantUML-safe identifier (letters, digits, underscores)."""
    key = re.sub(r"\W+", "_", raw, flags=re.ASCII).strip("_") or "node"
    return f"n_{key}" if key[0].isdigit() else key


def _text(text: str) -> str:
    """Single-line text for a quoted label, title or note (a newline would end the statement)."""
    return " ".join(text.split()).replace('"', "'")

"""
Mermaid.js diagram generator.

Produces Mermaid syntax for all four architecture levels:
- L0: System Context (flowchart with external systems)
- L1: Module Architecture (flowchart with dependency arrows)
- L2: Component Detail (class diagram within a module)
- L3: Process Flows (sequence diagram)

Output is GitHub-compatible Mermaid markdown that renders
natively in README files and PR comments.
"""

from __future__ import annotations

import re
from pathlib import Path

from archilens.config import ArchiLensConfig
from archilens.generators.base import (
    L1_MAX_EDGES,
    L2_MAX_COMPONENTS,
    STORES,
    DiagramGenerator,
    component_kind,
    l0_systems,
    l2_note,
    member_names,
    omitted_note,
    select_l1_edges,
    select_l2,
    shorten,
    stroke_width,
)
from archilens.models import (
    ArchSnapshot,
    EdgeType,
    NodeType,
    ProcessFlow,
)


class MermaidGenerator(DiagramGenerator):
    """Generates GitHub-native Mermaid.js diagrams."""

    extension = "md"

    def system_context(self, snapshot: ArchSnapshot) -> str:
        return generate_system_context(snapshot, self.config)

    def module_architecture(self, snapshot: ArchSnapshot) -> str:
        return generate_module_architecture(snapshot, self.config.diagrams.l1_max_edges)

    def component_detail(self, snapshot: ArchSnapshot, module_id: str) -> str | None:
        return generate_component_detail(snapshot, module_id, self.config.diagrams.l2_max_components)

    def process_flow(self, flow: ProcessFlow) -> str:
        return generate_process_flow(flow)


def generate_all_diagrams(
    snapshot: ArchSnapshot,
    config: ArchiLensConfig,
    output_dir: str | Path,
) -> list[Path]:
    """Generate all configured diagram levels (Mermaid format)."""
    return MermaidGenerator(config).generate_all(snapshot, output_dir)


# ---------------------------------------------------------------------------
# L0: System Context
# ---------------------------------------------------------------------------


def generate_system_context(
    snapshot: ArchSnapshot,
    config: ArchiLensConfig,
) -> str:
    """Generate L0 System Context diagram."""
    lines = [
        f"# L0: System Context — {snapshot.project_name}",
        "",
        "```mermaid",
        "flowchart TB",
    ]

    # Style definitions
    lines.extend(
        [
            "    classDef system fill:#1168bd,stroke:#5aa0e6,color:#fff",
            "    classDef external fill:#64748b,stroke:#94a3b8,color:#fff",
            "    classDef actor fill:#08427b,stroke:#4a86c8,color:#fff",
            "    classDef database fill:#2a74c4,stroke:#7cb4ec,color:#fff",
            "",
        ]
    )

    # Central system node
    system_id = "SYSTEM"
    lines.append(f'    {system_id}["{escape_label(snapshot.project_name)}"]:::system')
    lines.append("")

    # External systems: declared in the config, then detected from imports
    systems = l0_systems(snapshot, config)
    for name, kind, description, _ in systems:
        ext_id = sanitize_id(name)
        label = escape_label(name)
        if description:
            label += f"<br/><small>{escape_label(shorten(description, 70))}</small>"
        if kind in STORES:
            lines.append(f'    {ext_id}[("{label}")]:::database')
        else:
            lines.append(f'    {ext_id}["{label}"]:::external')

        lines.append(f"    {system_id} --> {ext_id}")

    # Actors (if any flows have triggers suggesting user interaction)
    has_user_facing = any(f.trigger.startswith(("GET", "POST", "PUT", "DELETE", "PATCH")) for f in snapshot.flows)
    if has_user_facing:
        lines.append('    USER["User / Client"]:::actor')
        lines.append(f"    USER --> {system_id}")

    lines.append("```")
    lines.append("")

    if systems:
        lines += ["## External Systems", "", "| System | Type | Source | Used by |", "|---|---|---|---|"]
        detected = {d.name: d for d in snapshot.detected_systems}
        for name, kind, _, source in systems:
            users = ", ".join(f"`{m}`" for m in detected[name].modules) if name in detected else ""
            lines.append(f"| {_cell(name)} | {kind.replace('_', ' ')} | {_cell(source)} | {_cell(users)} |")
        lines.append("")
        if detected:
            lines += [
                "<sub>Detected systems come from third-party imports. Declare a system in "
                "`external_systems` to describe it yourself, or set "
                "`analysis.detect_external_systems: false` to turn detection off.</sub>",
                "",
            ]

    # Add detected patterns
    if snapshot.detected_patterns:
        lines.append("## Detected Architectural Patterns")
        lines.append("")
        for pattern in snapshot.detected_patterns:
            lines.append(f"- **{pattern.value.replace('_', ' ').title()}**")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# L1: Module Architecture
# ---------------------------------------------------------------------------


def generate_module_architecture(snapshot: ArchSnapshot, max_edges: int = L1_MAX_EDGES) -> str:
    """
    Generate L1 Module Architecture diagram. Line width shows the import count instead of
    a label per arrow, and at most *max_edges* dependencies are drawn (0 = all); the
    table below the diagram lists every one.
    """
    module_nodes = snapshot.get_module_nodes()
    if not module_nodes:
        return "# L1: Module Architecture\n\nNo modules detected."

    node_ids = {n.id for n in module_nodes}
    edges = snapshot.get_edges_for_nodes(node_ids)

    lines = [
        f"# L1: Module Architecture — {snapshot.project_name}",
        "",
        "```mermaid",
        "flowchart LR",
    ]

    # Style
    lines.extend(
        [
            "    classDef module fill:#1168bd,stroke:#5aa0e6,color:#fff",
            "    classDef small fill:#2a74c4,stroke:#7cb4ec,color:#fff",
            "",
        ]
    )

    # Group by business capability if available
    if snapshot.capability_map:
        for cap_name, mod_ids in snapshot.capability_map.items():
            cap_id = sanitize_id(cap_name)
            lines.append(f'    subgraph {cap_id}["{escape_label(cap_name)}"]')
            for mod_id in mod_ids:
                node = next((n for n in module_nodes if n.id == mod_id), None)
                if node:
                    safe_id = sanitize_id(node.id)
                    label = escape_label(node.name)
                    if node.ai_summary:
                        label += f"<br/><small>{escape_label(shorten(node.ai_summary, 60))}</small>"
                    lines.append(f'        {safe_id}["{label}"]:::module')
            lines.append("    end")
            lines.append("")

    # Nodes not in any capability group
    grouped_ids = set()
    for mod_ids in snapshot.capability_map.values():
        grouped_ids.update(mod_ids)

    for node in module_nodes:
        if node.id not in grouped_ids:
            safe_id = sanitize_id(node.id)
            label = escape_label(node.name)
            if node.lines_of_code > 0:
                label += f"<br/><small>{node.lines_of_code} LOC</small>"
            lines.append(f'    {safe_id}["{label}"]:::module')

    lines.append("")

    # Edges: width from the import count (grouped into one linkStyle per width)
    shown = select_l1_edges(edges, max_edges)
    heaviest = max((e.weight for e in shown), default=1)
    widths: dict[float, list[int]] = {}
    for index, edge in enumerate(shown):
        lines.append(f"    {sanitize_id(edge.source)} --> {sanitize_id(edge.target)}")
        widths.setdefault(stroke_width(edge.weight, heaviest), []).append(index)
    for width, indices in sorted(widths.items()):
        lines.append(f"    linkStyle {','.join(map(str, indices))} stroke-width:{width:g}px")

    lines.append("```")
    lines.append("")
    note = omitted_note(len(shown), len(edges))
    lines.append(f"<sub>Line width shows the number of imports.{' ' + note + '.' if note else ''}</sub>")
    lines.append("")
    if snapshot.unanalyzed:
        lines.append(f"> **{_cell(snapshot.unanalyzed_note())}.**")
        lines.append("")

    # Module details table
    lines.append("## Module Details")
    lines.append("")
    lines.append("| Module | Lines of Code | Dependencies | Description |")
    lines.append("|--------|--------------|--------------|-------------|")
    for node in sorted(module_nodes, key=lambda n: n.lines_of_code, reverse=True):
        dep_count = sum(1 for e in edges if e.source == node.id)
        desc = _cell(node.ai_summary or "")
        lines.append(f"| {node.name} | {node.lines_of_code:,} | {dep_count} | {desc} |")
    lines.append("")

    if edges:
        names = {n.id: n.name for n in module_nodes}
        lines += [f"<details><summary>All {len(edges)} dependencies</summary>", ""]
        lines += ["| From | To | Imports |", "|------|----|---------|"]
        for edge in sorted(edges, key=lambda e: (-e.weight, e.source, e.target)):
            lines.append(
                f"| {_cell(names.get(edge.source, edge.source))} | "
                f"{_cell(names.get(edge.target, edge.target))} | {edge.weight} |"
            )
        lines += ["", "</details>", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# L2: Component Detail
# ---------------------------------------------------------------------------


def generate_component_detail(
    snapshot: ArchSnapshot,
    module_id: str,
    max_components: int = L2_MAX_COMPONENTS,
) -> str | None:
    """
    Generate L2 Component Detail diagram for a specific module: its classes and its files of
    functions, with inheritance and the imports between them. At most *max_components* boxes
    are drawn (0 = all); the table under the diagram lists the rest.
    """
    children, edges, everything = select_l2(snapshot, module_id, max_components)
    if not children:
        return None

    module_node = next((n for n in snapshot.nodes if n.id == module_id), None)
    if not module_node:
        return None

    lines = [
        f"# L2: Component Detail — {module_node.name}",
        "",
        "```mermaid",
        "classDiagram",
    ]

    # Position-based IDs: two classes with the same name in different files stay
    # separate boxes, and names with generics or symbols can't break the syntax.
    class_id = {child.id: f"C{i}" for i, child in enumerate(children)}
    for child in children:
        cid = class_id[child.id]
        lines.append(f'    class {cid}["{escape_label(child.name)}"] {{')
        if child.node_type == NodeType.INTERFACE:
            lines.append("        <<interface>>")
        elif child.node_type == NodeType.FUNCTION:
            lines.append("        <<functions>>")
        if child.file_path:
            lines.append(f"        +{_member_text(child.file_path)}")
        names, listed = member_names(child)
        if listed:
            lines.append(f"        {listed}")
        lines += [f"        +{_member_text(name)}()" for name in names]
        lines.append("    }")

    # Relationships
    imports = 0
    for edge in edges:
        if edge.source in class_id and edge.target in class_id:
            src_name, tgt_name = class_id[edge.source], class_id[edge.target]

            if edge.edge_type == EdgeType.INHERITANCE:
                lines.append(f"    {tgt_name} <|-- {src_name}")
            elif edge.edge_type == EdgeType.IMPLEMENTATION:
                lines.append(f"    {tgt_name} <|.. {src_name}")
            elif edge.edge_type == EdgeType.COMPOSITION:
                lines.append(f"    {src_name} *-- {tgt_name}")
            elif edge.edge_type == EdgeType.DEPENDENCY:
                lines.append(f"    {src_name} ..> {tgt_name}")
                imports += 1
            else:
                lines.append(f"    {src_name} --> {tgt_name} : {escape_label(edge.label or 'uses')}")

    lines.append("```")

    notes = []
    if imports:
        notes.append("A dashed arrow is an import: the file it starts from imports the file it points to.")
    note = l2_note(len(children), len(everything))
    if note:
        notes.append(note + ".")
    if notes:
        lines += ["", f"<sub>{' '.join(notes)}</sub>"]
    if note:
        lines += ["", f"<details><summary>All {len(everything)} components</summary>", ""]
        lines += ["| Component | Kind | File |", "|-----------|------|------|"]
        for child in everything:
            lines.append(f"| {_cell(child.name)} | {component_kind(child)} | {_cell(child.file_path or '')} |")
        lines += ["", "</details>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# L3: Process Flow (Sequence Diagram)
# ---------------------------------------------------------------------------


def generate_process_flow(flow: ProcessFlow) -> str:
    """Generate L3 Process Flow as a Mermaid sequence diagram."""
    lines = [
        f"# L3: {flow.name}",
        "",
        f"> **Trigger:** {flow.trigger}" if flow.trigger else "",
        f"> {flow.description}" if flow.description else "",
        "",
        *sequence_diagram(flow),
    ]
    return "\n".join(lines)


def sequence_diagram(flow: ProcessFlow) -> list[str]:
    """The fenced Mermaid sequence diagram of *flow*, as lines."""
    lines = ["```mermaid", "sequenceDiagram"]

    # Collect unique actors
    actors = []
    seen: set[str] = set()
    for step in flow.steps:
        for actor in [step.actor, step.target]:
            if actor not in seen:
                seen.add(actor)
                actors.append(actor)

    # Declare participants (IDs are position-based, so any actor name is safe)
    participant = {actor: f"p{i}" for i, actor in enumerate(actors)}
    for actor in actors:
        lines.append(f"    participant {participant[actor]} as {_sequence_text(actor)}")

    lines.append("")

    # Steps: solid arrow for a call, open arrow ("-)") for an asynchronous message
    for step in flow.steps:
        src, tgt = participant[step.actor], participant[step.target]

        if step.condition:
            lines.append(f"    alt {_sequence_text(step.condition)}")

        arrow = "-)" if step.is_async else "->>"
        label = step.action
        if step.data:
            label += f" [{step.data}]"

        lines.append(f"    {src}{arrow}{tgt}: {_sequence_text(label)}")

        if step.condition:
            lines.append("    end")

    lines.append("```")
    return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def escape_label(text: str) -> str:
    """Escape free text (names, AI summaries) for use inside a quoted Mermaid label."""
    return (
        " ".join(text.split())  # newlines would end the statement
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
    )


def _member_text(text: str) -> str:
    """
    Text for one line inside a class box. Mermaid reads a line containing brackets as a
    method and braces as the end of the class, so a path such as ``routes/(app)/[id].ts``
    needs them as character codes.
    """
    text = " ".join(text.split()).replace("#", "#35;")
    for char in '(){}~"<>':
        text = text.replace(char, f"#{ord(char)};")
    return text


def _cell(text: str) -> str:
    """Escape free text for a markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def sanitize_id(raw: str) -> str:
    """Convert a node ID to a Mermaid-safe identifier: ASCII letters, digits and underscores."""
    return re.sub(r"\W", "_", raw, flags=re.ASCII) or "node"


def _sequence_text(text: str) -> str:
    """
    Escape text for a sequence diagram. ``;`` ends a statement and ``#`` starts an
    entity code there, so they are written as entity codes too (single pass, so
    the ``;`` inside an inserted code is never re-escaped).
    """
    codes = {"#": "#35;", ";": "#59;", '"': "#quot;", "<": "#lt;", ">": "#gt;"}
    return re.sub(r'[#;"<>]', lambda m: codes[m.group(0)], " ".join(text.split()))

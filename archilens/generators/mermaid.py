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

from pathlib import Path

from archilens.config import ArchiLensConfig
from archilens.generators.base import DiagramGenerator
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
        return generate_module_architecture(snapshot)

    def component_detail(self, snapshot: ArchSnapshot, module_id: str) -> str | None:
        return generate_component_detail(snapshot, module_id)

    def process_flow(self, flow: ProcessFlow) -> str:
        return generate_process_flow(flow)

    def index(self, snapshot: ArchSnapshot, generated: list[Path]) -> str:
        return _generate_index(snapshot, generated, self.config)


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
            "    classDef system fill:#1168bd,stroke:#0b4884,color:#fff",
            "    classDef external fill:#999,stroke:#666,color:#fff",
            "    classDef actor fill:#08427b,stroke:#052e56,color:#fff",
            "    classDef database fill:#438dd5,stroke:#2e6295,color:#fff",
            "",
        ]
    )

    # Central system node
    system_id = "SYSTEM"
    lines.append(f'    {system_id}["{snapshot.project_name}"]:::system')
    lines.append("")

    # External systems
    for ext in config.external_systems:
        ext_id = _sanitize_id(ext.name)

        if ext.type == "database" or ext.type == "cache":
            lines.append(f'    {ext_id}[("{ext.name}<br/><small>{ext.description}</small>")]:::database')
        else:
            lines.append(f'    {ext_id}["{ext.name}<br/><small>{ext.description}</small>"]:::external')

        lines.append(f"    {system_id} --> {ext_id}")

    # Actors (if any flows have triggers suggesting user interaction)
    has_user_facing = any(f.trigger.startswith(("GET", "POST", "PUT", "DELETE", "PATCH")) for f in snapshot.flows)
    if has_user_facing:
        lines.append('    USER["User / Client"]:::actor')
        lines.append(f"    USER --> {system_id}")

    lines.append("```")
    lines.append("")

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


def generate_module_architecture(snapshot: ArchSnapshot) -> str:
    """Generate L1 Module Architecture diagram."""
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
            "    classDef module fill:#1168bd,stroke:#0b4884,color:#fff",
            "    classDef small fill:#438dd5,stroke:#2e6295,color:#fff",
            "",
        ]
    )

    # Group by business capability if available
    if snapshot.capability_map:
        for cap_name, mod_ids in snapshot.capability_map.items():
            cap_id = _sanitize_id(cap_name)
            lines.append(f'    subgraph {cap_id}["{cap_name}"]')
            for mod_id in mod_ids:
                node = next((n for n in module_nodes if n.id == mod_id), None)
                if node:
                    safe_id = _sanitize_id(node.id)
                    label = node.name
                    if node.ai_summary:
                        label += f"<br/><small>{node.ai_summary[:60]}</small>"
                    lines.append(f'        {safe_id}["{label}"]:::module')
            lines.append("    end")
            lines.append("")

    # Nodes not in any capability group
    grouped_ids = set()
    for mod_ids in snapshot.capability_map.values():
        grouped_ids.update(mod_ids)

    for node in module_nodes:
        if node.id not in grouped_ids:
            safe_id = _sanitize_id(node.id)
            label = node.name
            if node.lines_of_code > 0:
                label += f"<br/><small>{node.lines_of_code} LOC</small>"
            lines.append(f'    {safe_id}["{label}"]:::module')

    lines.append("")

    # Edges
    for edge in edges:
        src = _sanitize_id(edge.source)
        tgt = _sanitize_id(edge.target)
        if edge.label and edge.edge_type == EdgeType.DEPENDENCY:
            lines.append(f"    {src} -->|{edge.label}| {tgt}")
        else:
            lines.append(f"    {src} --> {tgt}")

    lines.append("```")
    lines.append("")

    # Module details table
    lines.append("## Module Details")
    lines.append("")
    lines.append("| Module | Lines of Code | Dependencies | Description |")
    lines.append("|--------|--------------|--------------|-------------|")
    for node in sorted(module_nodes, key=lambda n: n.lines_of_code, reverse=True):
        dep_count = sum(1 for e in edges if e.source == node.id)
        desc = node.ai_summary or ""
        lines.append(f"| {node.name} | {node.lines_of_code:,} | {dep_count} | {desc} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# L2: Component Detail
# ---------------------------------------------------------------------------


def generate_component_detail(
    snapshot: ArchSnapshot,
    module_id: str,
) -> str | None:
    """Generate L2 Component Detail diagram for a specific module."""
    children = snapshot.get_children(module_id)
    if not children:
        return None

    module_node = next((n for n in snapshot.nodes if n.id == module_id), None)
    if not module_node:
        return None

    child_ids = {c.id for c in children}
    edges = [e for e in snapshot.edges if e.source in child_ids or e.target in child_ids]

    lines = [
        f"# L2: Component Detail — {module_node.name}",
        "",
        "```mermaid",
        "classDiagram",
    ]

    for child in children:
        safe_name = child.name.replace(" ", "_")
        if child.node_type == NodeType.CLASS:
            lines.append(f"    class {safe_name} {{")
            if child.file_path:
                lines.append(f"        +{child.file_path}")
            lines.append("    }")
        elif child.node_type == NodeType.INTERFACE:
            lines.append(f"    class {safe_name} {{")
            lines.append("        <<interface>>")
            lines.append("    }")

    # Relationships
    for edge in edges:
        src_node = next((n for n in children if n.id == edge.source), None)
        tgt_node = next((n for n in children if n.id == edge.target), None)

        if src_node and tgt_node:
            src_name = src_node.name.replace(" ", "_")
            tgt_name = tgt_node.name.replace(" ", "_")

            if edge.edge_type == EdgeType.INHERITANCE:
                lines.append(f"    {tgt_name} <|-- {src_name}")
            elif edge.edge_type == EdgeType.IMPLEMENTATION:
                lines.append(f"    {tgt_name} <|.. {src_name}")
            elif edge.edge_type == EdgeType.COMPOSITION:
                lines.append(f"    {src_name} *-- {tgt_name}")
            else:
                lines.append(f"    {src_name} --> {tgt_name} : {edge.label}")

    lines.append("```")
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
        "```mermaid",
        "sequenceDiagram",
    ]

    # Collect unique actors
    actors = []
    seen: set[str] = set()
    for step in flow.steps:
        for actor in [step.actor, step.target]:
            if actor not in seen:
                seen.add(actor)
                actors.append(actor)

    # Declare participants
    for actor in actors:
        safe = actor.replace(" ", "_")
        lines.append(f"    participant {safe} as {actor}")

    lines.append("")

    # Steps
    for step in flow.steps:
        src = step.actor.replace(" ", "_")
        tgt = step.target.replace(" ", "_")

        if step.condition:
            lines.append(f"    alt {step.condition}")

        arrow = "->>" if step.is_async else "->>+"
        label = step.action
        if step.data:
            label += f" [{step.data}]"

        lines.append(f"    {src}{arrow}{tgt}: {label}")

        if step.condition:
            lines.append("    end")

    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Index / Navigation
# ---------------------------------------------------------------------------


def _generate_index(
    snapshot: ArchSnapshot,
    generated_files: list[Path],
    config: ArchiLensConfig,
) -> str:
    """Generate an index markdown file linking all diagrams."""
    lines = [
        f"# ArchiLens — {snapshot.project_name}",
        "",
        f"*Generated from `{snapshot.git_ref or 'HEAD'}` at {snapshot.timestamp}*",
        "",
        "## Architecture Diagrams",
        "",
        "| Level | Diagram | Description |",
        "|-------|---------|-------------|",
    ]

    for f in generated_files:
        if f.name == "INDEX.md":
            continue
        name = f.stem.replace("_", " ")
        level = name[:2] if name[:2] in ["L0", "L1", "L2", "L3"] else "--"
        lines.append(f"| {level} | [{name}]({f.name}) | |")

    lines.append("")

    # Business capabilities section
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_id(raw: str) -> str:
    """Convert a node ID to a Mermaid-safe identifier."""
    return (
        raw.replace(":", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
        .replace(" ", "_")
        .replace("<", "")
        .replace(">", "")
    )

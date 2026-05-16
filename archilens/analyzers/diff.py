"""
Architecture diff engine.

Compares two ArchSnapshots and produces a structured diff
showing what changed architecturally. Used for PR comments
and CI/CD drift detection.
"""

from __future__ import annotations

from archilens.config import CIRule
from archilens.models import ArchDiff, ArchSnapshot, DiffEntry


def compute_diff(base: ArchSnapshot, head: ArchSnapshot) -> ArchDiff:
    """
    Compare two architecture snapshots and return the diff.

    Detects:
    - Added/removed modules (nodes)
    - Added/removed dependencies (edges)
    - Added/removed process flows
    - Changes in dependency weight
    """
    diff = ArchDiff(base_ref=base.git_ref, head_ref=head.git_ref)

    # --- Node diff ---
    base_node_ids = {n.id for n in base.nodes}
    head_node_ids = {n.id for n in head.nodes}

    for node_id in head_node_ids - base_node_ids:
        node = next(n for n in head.nodes if n.id == node_id)
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="node",
                entity_id=node_id,
                description=f"New module added: {node.name}",
                severity="info",
            )
        )

    for node_id in base_node_ids - head_node_ids:
        node = next(n for n in base.nodes if n.id == node_id)
        diff.entries.append(
            DiffEntry(
                change_type="removed",
                entity_type="node",
                entity_id=node_id,
                description=f"Module removed: {node.name}",
                severity="warning",
            )
        )

    # --- Edge diff ---
    base_edge_keys = {(e.source, e.target, e.edge_type) for e in base.edges}
    head_edge_keys = {(e.source, e.target, e.edge_type) for e in head.edges}

    for key in head_edge_keys - base_edge_keys:
        src, tgt, etype = key
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="edge",
                entity_id=f"{src}->{tgt}",
                description=f"New dependency: {src} -> {tgt} ({etype.value})",
                severity="info",
            )
        )

    for key in base_edge_keys - head_edge_keys:
        src, tgt, etype = key
        diff.entries.append(
            DiffEntry(
                change_type="removed",
                entity_type="edge",
                entity_id=f"{src}->{tgt}",
                description=f"Dependency removed: {src} -> {tgt} ({etype.value})",
                severity="info",
            )
        )

    # --- Flow diff ---
    base_flow_ids = {f.id for f in base.flows}
    head_flow_ids = {f.id for f in head.flows}

    for flow_id in head_flow_ids - base_flow_ids:
        flow = next(f for f in head.flows if f.id == flow_id)
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="flow",
                entity_id=flow_id,
                description=f"New process flow: {flow.name}",
                severity="info",
            )
        )

    # Build summary
    added = sum(1 for e in diff.entries if e.change_type == "added")
    removed = sum(1 for e in diff.entries if e.change_type == "removed")
    modified = sum(1 for e in diff.entries if e.change_type == "modified")
    diff.summary = (
        f"Architecture changes: {added} added, {removed} removed, "
        f"{modified} modified across {len(diff.entries)} total changes."
    )

    return diff


def check_rules(
    diff: ArchDiff,
    head: ArchSnapshot,
    rules: list[CIRule],
) -> list[str]:
    """
    Check architecture rules against the current snapshot and diff.

    Returns list of violation messages.
    """
    violations: list[str] = []

    for rule in rules:
        if rule.name == "no-layer-skip" and rule.from_pattern and rule.to_pattern:
            # Check for forbidden dependencies
            for edge in head.edges:
                src_path = edge.source.replace("module:", "")
                tgt_path = edge.target.replace("module:", "")

                import fnmatch

                if fnmatch.fnmatch(src_path, rule.from_pattern.replace("**", "*")) and fnmatch.fnmatch(
                    tgt_path, rule.to_pattern.replace("**", "*")
                ):
                    msg = (
                        f"[{rule.action.upper()}] Rule '{rule.name}': "
                        f"Forbidden dependency from {src_path} to {tgt_path}"
                    )
                    violations.append(msg)

        elif rule.name == "max-fan-out" and rule.threshold:
            # Check fan-out
            from collections import Counter

            fan_out = Counter(e.source for e in head.edges)
            for node_id, count in fan_out.items():
                if count > rule.threshold:
                    node_name = node_id.replace("module:", "")
                    msg = (
                        f"[{rule.action.upper()}] Rule '{rule.name}': "
                        f"Module {node_name} has {count} dependencies "
                        f"(threshold: {rule.threshold})"
                    )
                    violations.append(msg)

    diff.rule_violations = violations
    return violations


def format_diff_as_markdown(diff: ArchDiff) -> str:
    """Format the architecture diff as a GitHub PR comment (emoji-free for terminal safety)."""
    lines = [
        "## ArchiLens: Architecture Diff",
        "",
        f"Comparing `{diff.base_ref}` -> `{diff.head_ref}`",
        "",
        diff.summary,
        "",
    ]

    if diff.has_breaking_changes:
        lines.append("> **WARNING: Breaking architectural changes detected!**")
        lines.append("")

    if diff.entries:
        lines.append("### Changes")
        lines.append("")
        lines.append("| Type | Change | Details | Severity |")
        lines.append("|------|--------|---------|----------|")

        symbol = {"added": "+", "removed": "-", "modified": "~"}
        for entry in diff.entries:
            icon = symbol.get(entry.change_type, "?")
            lines.append(
                f"| {entry.entity_type} | [{icon}] {entry.change_type} | {entry.description} | {entry.severity} |"
            )
        lines.append("")

    if diff.rule_violations:
        lines.append("### Rule Violations")
        lines.append("")
        for v in diff.rule_violations:
            lines.append(f"- {v}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by [ArchiLens](https://github.com/your-org/archilens)*")

    return "\n".join(lines)

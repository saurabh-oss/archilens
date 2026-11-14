"""
Architecture diff engine.

Compares two ArchSnapshots and produces a structured diff
showing what changed architecturally, and evaluates CI rules
against it. ``archilens.generators.diff_report`` renders the
result as the PR comment.
"""

from __future__ import annotations

from archilens.config import CIRule
from archilens.models import ArchDiff, ArchEdge, ArchNode, ArchSnapshot, DiffEntry, EdgeType, NodeType
from archilens.rules import evaluate_rules

_MODULE = "module:"

# Flag a node / edge as "modified" when its LOC / import count changes by this fraction.
_LOC_CHANGE_THRESHOLD = 0.2
_WEIGHT_CHANGE_THRESHOLD = 0.2


def compute_diff(base: ArchSnapshot, head: ArchSnapshot) -> ArchDiff:
    """
    Compare two architecture snapshots and return the diff.

    Detects:
    - Added/removed modules and classes (nodes)
    - Classes that moved to another file, reported once per file move
    - Added/removed dependencies (edges)
    - Added/removed process flows
    - Changes in dependency weight
    """
    diff = ArchDiff(base_ref=base.git_ref, head_ref=head.git_ref)

    # --- Node diff ---
    # Every loop below iterates in sorted order: set order changes between Python
    # processes (hash randomisation), which would reshuffle the PR comment on each run.
    base_nodes = {n.id: n for n in base.nodes}
    head_nodes = {n.id: n for n in head.nodes}
    base_node_ids = set(base_nodes)
    head_node_ids = set(head_nodes)

    # A class whose file was renamed or moved would otherwise appear as one
    # removal plus one addition; pair them and report one entry per file move.
    moved_from, moved_to = _match_moved_classes(
        [base_nodes[i] for i in base_node_ids - head_node_ids],
        [head_nodes[i] for i in head_node_ids - base_node_ids],
    )
    for (old_file, new_file), names in sorted(_group_moves(moved_from, moved_to, base_nodes, head_nodes).items()):
        diff.entries.append(
            DiffEntry(
                change_type="moved",
                entity_type="node",
                entity_id=f"{old_file}->{new_file}",
                description=(
                    f"Moved {len(names)} class{'es' if len(names) > 1 else ''} from {old_file} "
                    f"to {new_file}: {', '.join(names)}"
                ),
                severity="info",
            )
        )
    base_node_ids -= set(moved_from)
    head_node_ids -= set(moved_to)

    for node_id in sorted(head_node_ids - base_node_ids):
        node = head_nodes[node_id]
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="node",
                entity_id=node_id,
                description=f"New {_describe(node)} added",
                severity="info",
            )
        )

    for node_id in sorted(base_node_ids - head_node_ids):
        node = base_nodes[node_id]
        diff.entries.append(
            DiffEntry(
                change_type="removed",
                entity_type="node",
                entity_id=node_id,
                description=f"{_describe(node)[0].upper()}{_describe(node)[1:]} removed",
                severity="warning" if node.node_type == NodeType.MODULE else "info",
            )
        )

    # --- Modified nodes (same node, LOC changed by >20%) ---
    for node_id in sorted(base_node_ids & head_node_ids):
        base_node = base_nodes[node_id]
        head_node = head_nodes[node_id]
        bloc = base_node.lines_of_code
        hloc = head_node.lines_of_code
        if bloc > 0 and abs(hloc - bloc) / bloc >= _LOC_CHANGE_THRESHOLD:
            direction = "grew" if hloc > bloc else "shrank"
            diff.entries.append(
                DiffEntry(
                    change_type="modified",
                    entity_type="node",
                    entity_id=node_id,
                    description=f"Module {base_node.name} {direction}: {bloc:,} -> {hloc:,} LOC",
                    severity="info",
                )
            )

    # --- Edge diff ---
    base_edges, head_edges = _reported_edges(base), _reported_edges(head)
    base_edge_keys = {(e.source, e.target, e.edge_type) for e in base_edges}
    head_edge_keys = {(e.source, e.target, e.edge_type) for e in head_edges}

    for key in sorted(head_edge_keys - base_edge_keys, key=_edge_sort_key):
        src, tgt, etype = key
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="edge",
                entity_id=f"{src}->{tgt}",
                description=f"New dependency: {_edge_text(src, tgt, etype)}",
                severity="info",
                source=src,
                target=tgt,
                edge_type=etype.value,
            )
        )

    for key in sorted(base_edge_keys - head_edge_keys, key=_edge_sort_key):
        src, tgt, etype = key
        diff.entries.append(
            DiffEntry(
                change_type="removed",
                entity_type="edge",
                entity_id=f"{src}->{tgt}",
                description=f"Dependency removed: {_edge_text(src, tgt, etype)}",
                severity="info",
                source=src,
                target=tgt,
                edge_type=etype.value,
            )
        )

    # --- Modified edges (same key, changed weight — coupling increased/decreased) ---
    base_edge_weights = {(e.source, e.target, e.edge_type): e.weight for e in base_edges}
    head_edge_weights = {(e.source, e.target, e.edge_type): e.weight for e in head_edges}

    for key in sorted(base_edge_keys & head_edge_keys, key=_edge_sort_key):
        bw = base_edge_weights[key]
        hw = head_edge_weights[key]
        if bw > 0 and abs(hw - bw) / bw >= _WEIGHT_CHANGE_THRESHOLD:
            src, tgt, etype = key
            direction = "increased" if hw > bw else "decreased"
            diff.entries.append(
                DiffEntry(
                    change_type="modified",
                    entity_type="edge",
                    entity_id=f"{src}->{tgt}",
                    description=f"Coupling {direction}: {_edge_text(src, tgt)} ({bw} -> {hw} imports)",
                    severity="info",
                    source=src,
                    target=tgt,
                    edge_type=etype.value,
                    before=bw,
                    after=hw,
                )
            )

    # --- Flow diff ---
    base_flow_ids = {f.id for f in base.flows}
    head_flow_ids = {f.id for f in head.flows}

    head_flows = {f.id: f for f in head.flows}
    for flow_id in sorted(head_flow_ids - base_flow_ids):
        flow = head_flows[flow_id]
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="flow",
                entity_id=flow_id,
                description=f"New process flow: {flow.name}",
                severity="info",
            )
        )

    # --- External systems (detected from third-party imports) ---
    base_systems = {s.name: s for s in base.detected_systems}
    head_systems = {s.name: s for s in head.detected_systems}
    for name in sorted(head_systems.keys() - base_systems.keys()):
        system = head_systems[name]
        diff.entries.append(
            DiffEntry(
                change_type="added",
                entity_type="external",
                entity_id=f"external:{name}",
                description=f"New external system: {name} ({_kind(system.type)}), {system.used_by()}",
                severity="warning",  # the system now depends on something outside itself
            )
        )
    for name in sorted(base_systems.keys() - head_systems.keys()):
        diff.entries.append(
            DiffEntry(
                change_type="removed",
                entity_type="external",
                entity_id=f"external:{name}",
                description=f"External system no longer used: {name}",
                severity="info",
            )
        )
    for name in sorted(head_systems.keys() & base_systems.keys()):
        newcomers = sorted(set(head_systems[name].modules) - set(base_systems[name].modules))
        if newcomers:
            who = ", ".join(m.removeprefix(_MODULE) for m in newcomers)
            diff.entries.append(
                DiffEntry(
                    change_type="modified",
                    entity_type="external",
                    entity_id=f"external:{name}",
                    description=f"{name} is now also used by {who}",
                    severity="info",
                )
            )

    # Build summary
    added = sum(1 for e in diff.entries if e.change_type == "added")
    removed = sum(1 for e in diff.entries if e.change_type == "removed")
    modified = sum(1 for e in diff.entries if e.change_type == "modified")
    moved = sum(1 for e in diff.entries if e.change_type == "moved")
    diff.summary = (
        f"Architecture changes: {added} added, {removed} removed, "
        f"{modified} modified{f', {moved} moved' if moved else ''} across {len(diff.entries)} total changes."
    )

    return diff


def _match_moved_classes(removed: list[ArchNode], added: list[ArchNode]) -> tuple[list[str], list[str]]:
    """
    Pair removed and added class nodes that are the same class in a new file.

    A pair is made only when the name (and node type) is unambiguous: exactly one
    removed and one added node carry it. Returns parallel lists of
    (base node id, head node id).
    """

    def by_name(nodes: list[ArchNode]) -> dict[tuple[str, str], list[ArchNode]]:
        groups: dict[tuple[str, str], list[ArchNode]] = {}
        for node in nodes:
            # A file of functions is named after its file, so it has no name to follow to a new one.
            if node.node_type not in (NodeType.MODULE, NodeType.FUNCTION) and node.file_path:
                groups.setdefault((node.node_type.value, node.name), []).append(node)
        return groups

    old, new = by_name(removed), by_name(added)
    pairs = sorted(
        (old[key][0].id, new[key][0].id)
        for key in old.keys() & new.keys()
        if len(old[key]) == 1 and len(new[key]) == 1 and old[key][0].file_path != new[key][0].file_path
    )
    return [a for a, _ in pairs], [b for _, b in pairs]


def _group_moves(
    moved_from: list[str], moved_to: list[str], base_nodes: dict[str, ArchNode], head_nodes: dict[str, ArchNode]
) -> dict[tuple[str, str], list[str]]:
    """(old file, new file) -> sorted class names that moved between them."""
    groups: dict[tuple[str, str], list[str]] = {}
    for old_id, new_id in zip(moved_from, moved_to, strict=True):
        old, new = base_nodes[old_id], head_nodes[new_id]
        groups.setdefault((old.file_path or "", new.file_path or ""), []).append(old.name)
    return {key: sorted(names) for key, names in groups.items()}


def _kind(system_type: str) -> str:
    """ "external_api" -> "external API" for prose."""
    return "external API" if system_type == "external_api" else system_type


def _reported_edges(snapshot: ArchSnapshot) -> list[ArchEdge]:
    """
    The relationships a diff reports: everything except imports between the files of one
    module. Those are what the L2 diagram draws; a pull request that adds one has not changed
    which modules depend on which.
    """
    return [e for e in snapshot.edges if e.edge_type != EdgeType.DEPENDENCY or e.source.startswith(_MODULE)]


def _describe(node: ArchNode) -> str:
    """Human label for a node in a diff entry, e.g. "module Orders" or "class Foo in app/orders"."""
    if node.node_type == NodeType.MODULE:
        return f"module {node.name}"
    if node.node_type == NodeType.FUNCTION:
        return f"file of functions {node.file_path}"
    where = f" in {node.parent.removeprefix('module:')}" if node.parent else ""
    return f"{node.node_type.value} {node.name}{where}"


def _edge_text(src: str, tgt: str, etype: EdgeType | None = None) -> str:
    """
    "orders -> payments" for the report: module IDs without their "module:" prefix, and the
    edge kind only when it isn't a plain dependency (e.g. "(inherits)").
    """
    text = f"{src.removeprefix(_MODULE)} -> {tgt.removeprefix(_MODULE)}"
    return text if etype in (None, EdgeType.DEPENDENCY) else f"{text} ({etype.value})"


def _edge_sort_key(key: tuple[str, str, EdgeType]) -> tuple[str, str, str]:
    src, tgt, etype = key
    return (src, tgt, etype.value)


def check_rules(
    diff: ArchDiff,
    head: ArchSnapshot,
    rules: list[CIRule],
    base: ArchSnapshot | None = None,
) -> list[str]:
    """
    Evaluate *rules* on *head* and record the result on *diff*.

    With *base*, violations that base already had are kept apart in
    ``diff.existing_violations``; only the rest are "new" (``diff.violations``).
    That lets a team adopt a rule on a codebase that breaks it today and still
    fail pull requests that make things worse. Without *base*, every violation
    is new.

    Returns the messages of the new violations (also ``diff.rule_violations``).
    """
    head_violations = evaluate_rules(head, rules)
    base_keys = {v.key for v in evaluate_rules(base, rules)} if base is not None else set()
    diff.violations = [v for v in head_violations if v.key not in base_keys]
    diff.existing_violations = [v for v in head_violations if v.key in base_keys]
    diff.rule_violations = [v.text() for v in diff.violations]
    return diff.rule_violations

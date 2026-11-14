"""
Core data models representing the architecture graph at all zoom levels.

The architecture is modeled as a directed graph where nodes represent
code modules, services, classes, or functions, and edges represent
dependencies, calls, data flows, or event emissions.

The graph supports four levels of abstraction (L0-L3), mirroring
the C4 model approach but extended with process flow capabilities.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DiagramLevel(int, Enum):
    """Abstraction levels for architecture diagrams."""

    SYSTEM_CONTEXT = 0  # L0: System as black box + external actors
    MODULE = 1  # L1: Major modules/services and their connections
    COMPONENT = 2  # L2: Internal components within a module
    PROCESS_FLOW = 3  # L3: Runtime behavior, request lifecycles


class NodeType(str, Enum):
    """Types of nodes in the architecture graph."""

    SYSTEM = "system"
    EXTERNAL_SYSTEM = "external_system"
    ACTOR = "actor"
    MODULE = "module"
    SERVICE = "service"
    COMPONENT = "component"
    CLASS = "class"
    FUNCTION = "function"
    INTERFACE = "interface"
    DATABASE = "database"
    QUEUE = "queue"
    CACHE = "cache"


class EdgeType(str, Enum):
    """Types of relationships between nodes."""

    DEPENDENCY = "dependency"  # Static import/require
    CALL = "call"  # Function/method invocation
    DATA_FLOW = "data_flow"  # Data passed between components
    EVENT = "event"  # Event emission/subscription
    HTTP = "http"  # HTTP request
    GRPC = "grpc"  # gRPC call
    INHERITANCE = "inheritance"  # Class inheritance
    IMPLEMENTATION = "implementation"  # Interface implementation
    COMPOSITION = "composition"  # Object composition


class ArchPattern(str, Enum):
    """Detected architectural patterns."""

    MVC = "mvc"
    HEXAGONAL = "hexagonal"
    LAYERED = "layered"
    MICROSERVICES = "microservices"
    EVENT_DRIVEN = "event_driven"
    CQRS = "cqrs"
    REPOSITORY = "repository"
    FACTORY = "factory"
    OBSERVER = "observer"
    MIDDLEWARE_CHAIN = "middleware_chain"
    SAGA = "saga"
    CLEAN_ARCHITECTURE = "clean_architecture"


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------


class ArchNode(BaseModel):
    """A node in the architecture graph."""

    id: str = Field(description="Unique identifier (e.g., 'src/orders/service.py')")
    name: str = Field(description="Human-readable display name")
    node_type: NodeType
    level: DiagramLevel
    description: str = ""

    # Source code location
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    # Metrics
    lines_of_code: int = 0
    complexity: float = 0.0

    # Business mapping
    capability: str | None = None

    # AI-generated annotation
    ai_summary: str | None = None
    # AI-suggested business capability, only for modules with none configured (never enforced)
    suggested_capability: str | None = None

    # Children for drill-down (populated at finer levels)
    children: list[str] = Field(default_factory=list)

    # Parent node ID (for upward navigation)
    parent: str | None = None


class ArchEdge(BaseModel):
    """A directed edge (relationship) in the architecture graph."""

    source: str = Field(description="Source node ID")
    target: str = Field(description="Target node ID")
    edge_type: EdgeType
    label: str = ""
    weight: int = 1  # Number of occurrences

    # For process flows: ordering
    sequence: int | None = None

    # For data flows: what data is passed
    data_schema: str | None = None


# ---------------------------------------------------------------------------
# Process Flows
# ---------------------------------------------------------------------------


class FlowStep(BaseModel):
    """A single step in a process flow (sequence diagram)."""

    order: int
    actor: str  # Who performs this step
    action: str  # What they do
    target: str  # Who they interact with
    data: str | None = None  # What data is exchanged
    is_async: bool = False
    condition: str | None = None  # Guard condition (if/else branch)


class ProcessFlow(BaseModel):
    """A complete process flow / request lifecycle."""

    id: str
    name: str
    description: str = ""
    trigger: str = ""  # What initiates this flow (e.g., "POST /orders")
    steps: list[FlowStep] = Field(default_factory=list)
    entry_point: str | None = None  # File path of the entry point


# ---------------------------------------------------------------------------
# Architecture Snapshot
# ---------------------------------------------------------------------------


class ArchSnapshot(BaseModel):
    """Complete architecture state at a point in time."""

    project_name: str
    project_type: str = ""  # monolith | microservices | library | service
    git_ref: str = ""  # commit SHA or tag
    timestamp: str = ""

    nodes: list[ArchNode] = Field(default_factory=list)
    edges: list[ArchEdge] = Field(default_factory=list)
    flows: list[ProcessFlow] = Field(default_factory=list)

    detected_patterns: list[ArchPattern] = Field(default_factory=list)

    # Mapping: capability name -> list of module IDs
    capability_map: dict[str, list[str]] = Field(default_factory=dict)

    #: Language name -> number of source files ArchiLens found but cannot analyse yet
    #: (e.g. {"C#": 120}). Their modules exist, with no dependencies or components.
    unanalyzed: dict[str, int] = Field(default_factory=dict)

    def unanalyzed_note(self) -> str:
        """One sentence saying which files were found but not analysed; '' if none."""
        if not self.unanalyzed:
            return ""
        parts = [
            f"{count:,} {name} file{'s' * (count != 1)}"
            for name, count in sorted(self.unanalyzed.items(), key=lambda item: (-item[1], item[0]))
        ]
        listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        return (
            f"Not analysed: {listed}. ArchiLens groups them into modules but does not read their imports "
            "or classes yet, so those modules show no dependencies"
        )

    def get_nodes_at_level(self, level: DiagramLevel) -> list[ArchNode]:
        """Return all nodes at a specific abstraction level."""
        return [n for n in self.nodes if n.level == level]

    def get_edges_for_nodes(self, node_ids: set[str]) -> list[ArchEdge]:
        """Return edges where both source and target are in the given node set."""
        return [e for e in self.edges if e.source in node_ids and e.target in node_ids]

    def get_children(self, node_id: str) -> list[ArchNode]:
        """Get child nodes for drill-down."""
        return [n for n in self.nodes if n.parent == node_id]

    def get_module_nodes(self) -> list[ArchNode]:
        """Shorthand: get all L1 module nodes."""
        return self.get_nodes_at_level(DiagramLevel.MODULE)


# ---------------------------------------------------------------------------
# Architecture Diff (for PR drift detection)
# ---------------------------------------------------------------------------


class DiffEntry(BaseModel):
    """A single change in the architecture."""

    change_type: str  # "added" | "removed" | "modified" | "moved"
    entity_type: str  # "node" | "edge" | "flow"
    entity_id: str
    description: str
    severity: str = "info"  # "info" | "warning" | "critical"
    # Edges: the endpoints and type; modified edges also carry the weight before/after.
    source: str | None = None
    target: str | None = None
    edge_type: str | None = None
    before: int | None = None
    after: int | None = None


class RuleViolation(BaseModel):
    """One violation of a CI architecture rule."""

    rule: str  # rule name from .archilens.yml
    rule_type: str  # forbidden-dependency | max-fan-out | no-cycles
    action: str  # warn | fail
    message: str
    #: Stable identity used to tell new violations from ones already on the base ref
    key: str
    modules: list[str] = Field(default_factory=list)

    def text(self) -> str:
        return f"[{self.action.upper()}] Rule '{self.rule}': {self.message}"


class AIUsage(BaseModel):
    """What a run's AI requests used. Thinking tokens count as output tokens."""

    requests: int = 0  # answered by the model
    cached: int = 0  # answered from the local cache, at no cost
    input_tokens: int = 0  # including prompt-cache reads and writes
    output_tokens: int = 0

    def text(self) -> str:
        """E.g. "2 requests: 8,912 input and 1,204 output tokens (1 more from cache)"."""
        if not self.requests:
            return f"answered from cache ({self.cached} request{'s' * (self.cached != 1)}, no tokens used)"
        used = (
            f"{self.requests} request{'s' * (self.requests != 1)}: "
            f"{self.input_tokens:,} input and {self.output_tokens:,} output tokens"
        )
        return used + (f" ({self.cached} more from cache)" if self.cached else "")


class DiffReview(BaseModel):
    """AI commentary on an architecture diff (``archilens diff --ai``)."""

    model: str  # the model that wrote it, credited in the report
    usage: AIUsage | None = None
    summary: str  # what the change does to the architecture, in a few sentences
    highlights: list[str] = Field(default_factory=list)  # points worth a reviewer's attention
    #: Module node ID -> one-line description, for modules the change adds
    module_descriptions: dict[str, str] = Field(default_factory=dict)
    #: Process flows of entry-point files the change adds
    flows: list[ProcessFlow] = Field(default_factory=list)


class ArchDiff(BaseModel):
    """Difference between two architecture snapshots."""

    base_ref: str
    head_ref: str
    entries: list[DiffEntry] = Field(default_factory=list)
    #: Messages of the violations introduced by head (``RuleViolation.text()``)
    rule_violations: list[str] = Field(default_factory=list)
    #: Violations introduced by head (not present on base)
    violations: list[RuleViolation] = Field(default_factory=list)
    #: Violations that head still has but base already had
    existing_violations: list[RuleViolation] = Field(default_factory=list)
    summary: str = ""
    review: DiffReview | None = None

    @property
    def has_breaking_changes(self) -> bool:
        return any(e.severity == "critical" for e in self.entries)

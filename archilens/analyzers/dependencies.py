"""
Dependency analyzer using Tree-sitter for multi-language AST parsing.

Extracts import statements, function calls, class hierarchies,
and other structural relationships from source code to build
the architecture dependency graph.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import networkx as nx

from archilens.analyzers.discovery import LANGUAGE_NAMES, Module, SourceFile, _candidate_paths
from archilens.analyzers.embedded import SCRIPT_HOSTS, script_source
from archilens.analyzers.layout import ROOT_MODULE
from archilens.analyzers.resolution import ImportResolver
from archilens.analyzers.treesitter import TSExtractionResult, extract_from_file
from archilens.models import (
    ArchEdge,
    ArchNode,
    DiagramLevel,
    EdgeType,
    NodeType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Language-specific import extraction (regex-based fallback)
# ---------------------------------------------------------------------------

# Patterns for extracting imports across languages. Its keys, plus the script hosts
# (Vue, Svelte), are the languages ArchiLens reads imports and classes from.
IMPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE),
        re.compile(r"^\s*from\s+([\w.]+)\s+import", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"""import\s+.*?\s+from\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE),
    ],
    "typescript": [
        re.compile(r"""import\s+.*?\s+from\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE),
    ],
    "java": [
        re.compile(r"^\s*import\s+([\w.]+);", re.MULTILINE),
    ],
    "go": [
        re.compile(r'"([\w./]+)"', re.MULTILINE),
    ],
}

# Patterns for detecting class definitions
CLASS_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*class\s+(\w+)(?:\(([^)]*)\))?:", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE),
    ],
    "typescript": [
        re.compile(r"class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?", re.MULTILINE),
    ],
    "java": [
        re.compile(r"class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?", re.MULTILINE),
    ],
}

# Patterns for detecting function/method definitions
FUNCTION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
        re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>", re.MULTILINE),
    ],
    "typescript": [
        re.compile(r"(?:async\s+)?function\s+(\w+)\s*[<(]", re.MULTILINE),
        re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>", re.MULTILINE),
    ],
    "java": [
        re.compile(r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(", re.MULTILINE),
    ],
}


@dataclass
class ImportInfo:
    """An extracted import statement."""

    source_file: str  # File containing the import
    imported_path: str  # What was imported (module path or package)
    is_relative: bool = False
    is_external: bool = False  # Third-party package


@dataclass
class ClassInfo:
    """An extracted class definition."""

    name: str
    file_path: str
    parent_classes: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    line_number: int = 0
    #: A single-file UI component (Vue, Svelte) rather than a class declaration
    is_component: bool = False


@dataclass
class FunctionInfo:
    """An extracted function/method definition."""

    name: str
    file_path: str
    line_number: int = 0
    is_method: bool = False
    parent_class: str | None = None


def analyze_dependencies(
    files: list[SourceFile],
    modules: dict[str, Module],
    repo_path: str | Path,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[list[ArchNode], list[ArchEdge], nx.DiGraph]:
    """
    Analyze all source files and build the architecture graph.

    Args:
        files:       All discovered source files.
        modules:     Module registry from discovery.
        repo_path:   Repository root path.
        progress_cb: Optional callable(relative_path) called after each file.

    Returns:
        Tuple of (nodes, edges, NetworkX directed graph)
    """
    repo_path = Path(repo_path).resolve()

    # Phase 1: Extract imports, classes, functions from all files
    all_imports: list[ImportInfo] = []
    all_classes: list[ClassInfo] = []
    all_functions: list[FunctionInfo] = []

    for source_file in files:
        if source_file.is_test:
            if progress_cb:
                progress_cb(source_file.relative_path)
            continue

        try:
            content = source_file.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            logger.warning("Cannot read %s — skipping", source_file.relative_path)
            if progress_cb:
                progress_cb(source_file.relative_path)
            continue

        try:
            # A Vue or Svelte file is parsed as the JavaScript/TypeScript in its <script> blocks.
            code, code_language = script_source(content, source_file.language)
            parsed = source_file
            if code_language != source_file.language:
                parsed = replace(source_file, language=code_language)
                # The file itself is the component, whether or not its script declares anything.
                all_classes.append(
                    ClassInfo(
                        name=source_file.path.stem,
                        file_path=source_file.relative_path,
                        line_number=1,
                        is_component=True,
                    )
                )
            ts_result = extract_from_file(source_file.path, code_language, code)
            all_imports.extend(_extract_imports(code, parsed, ts_result))
            all_classes.extend(_extract_classes(code, parsed, ts_result))
            all_functions.extend(_extract_functions(code, parsed, ts_result))
        except Exception as exc:
            logger.warning(
                "Error analysing %s — skipping file (%s)",
                source_file.relative_path,
                exc,
            )
        finally:
            if progress_cb:
                progress_cb(source_file.relative_path)

    # Build a lookup: file path -> module name
    file_to_module_map: dict[str, str] = {f.relative_path: f.module for f in files}

    # Phase 2: Build module-level nodes (L1)
    nodes: list[ArchNode] = []
    for mod_name, mod in modules.items():
        if all(f.is_test for f in mod.files):
            continue  # Skip test-only modules

        node = ArchNode(
            id=f"module:{mod_name}",
            name=_humanize_module_name(mod_name),
            node_type=NodeType.MODULE,
            level=DiagramLevel.MODULE,
            file_path=mod.path,
            lines_of_code=mod.total_lines,
            children=[],
        )
        nodes.append(node)

    # Phase 3: Build component-level nodes (L2) from classes
    module_nodes = {n.id: n for n in nodes}
    for cls in all_classes:
        parent_id = f"module:{file_to_module_map.get(cls.file_path, ROOT_MODULE)}"

        node = ArchNode(
            id=f"class:{cls.file_path}:{cls.name}",
            name=cls.name,
            node_type=NodeType.COMPONENT if cls.is_component else NodeType.CLASS,
            level=DiagramLevel.COMPONENT,
            file_path=cls.file_path,
            line_start=cls.line_number,
            parent=parent_id,
        )
        nodes.append(node)

        # Register as child of parent module
        if parent_id in module_nodes:
            module_nodes[parent_id].children.append(node.id)

    # Phase 4: Resolve imports to files, then aggregate at module level.
    # Each distinct (file, imported path) pair counts once, so an edge's weight is
    # the number of import statements across files, not repeated mentions.
    edges: list[ArchEdge] = []
    resolver = ImportResolver(repo_path, file_to_module_map, _candidate_paths(repo_path))
    language_of = {f.relative_path: f.language for f in files}
    module_deps: dict[tuple[str, str], int] = {}
    # Imports that are not files in this repository: the standard library and third-party
    # packages. analyzers.external reads external systems out of them.
    unresolved: list[tuple[str, str, str]] = []

    for source_path, imported_path in sorted({(imp.source_file, imp.imported_path) for imp in all_imports}):
        source_module = file_to_module_map.get(source_path, ROOT_MODULE)
        language = language_of.get(source_path, "")
        target_file = resolver.resolve(imported_path, language, source_path)
        target_module = file_to_module_map.get(target_file) if target_file else None
        if target_file is None:
            unresolved.append((source_module, imported_path, language))

        if target_module and target_module != source_module:
            key = (source_module, target_module)
            module_deps[key] = module_deps.get(key, 0) + 1

    for (src, tgt), weight in sorted(module_deps.items()):
        edge = ArchEdge(
            source=f"module:{src}",
            target=f"module:{tgt}",
            edge_type=EdgeType.DEPENDENCY,
            label=f"{weight} imports",
            weight=weight,
        )
        edges.append(edge)

    # Phase 5: Build inheritance edges (L2)
    class_names_map = {cls.name: cls for cls in all_classes}
    for cls in all_classes:
        for parent in cls.parent_classes:
            parent_name = parent.strip()
            if parent_name in class_names_map:
                pcls = class_names_map[parent_name]
                edge = ArchEdge(
                    source=f"class:{cls.file_path}:{cls.name}",
                    target=f"class:{pcls.file_path}:{pcls.name}",
                    edge_type=EdgeType.INHERITANCE,
                    label="extends",
                )
                edges.append(edge)

    # Phase 6: Build NetworkX graph for analysis
    graph = nx.DiGraph()
    graph.graph["unresolved_imports"] = unresolved  # (module, imported path, language)
    for node in nodes:
        graph.add_node(node.id, data=node)
    for edge in edges:
        if graph.has_node(edge.source) and graph.has_node(edge.target):
            graph.add_edge(edge.source, edge.target, data=edge)

    return nodes, edges, graph


def compute_metrics(graph: nx.DiGraph) -> dict[str, dict[str, float]]:
    """Compute architectural metrics from the dependency graph."""
    metrics: dict[str, dict[str, float]] = {}

    for node_id in graph.nodes:
        metrics[node_id] = {
            "fan_in": graph.in_degree(node_id),
            "fan_out": graph.out_degree(node_id),
            "betweenness": 0.0,
        }

    # Betweenness centrality (how much a module is a "bridge")
    if len(graph.nodes) > 2:
        try:
            centrality = nx.betweenness_centrality(graph)
            for node_id, value in centrality.items():
                metrics[node_id]["betweenness"] = round(value, 4)
        except Exception:
            pass

    # Detect cycles (architectural smell). A node is on a cycle iff its strongly
    # connected component has more than one node, or it has a self-loop.
    # (nx.simple_cycles would enumerate every cycle, which is exponential.)
    for component in nx.strongly_connected_components(graph):
        if len(component) > 1:
            for node_id in component:
                metrics[node_id]["in_cycle"] = 1.0
    for node_id in nx.nodes_with_selfloops(graph):
        metrics[node_id]["in_cycle"] = 1.0

    return metrics


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_imports(
    content: str,
    source_file: SourceFile,
    ts_result: TSExtractionResult | None = None,
) -> list[ImportInfo]:
    """Extract import statements, preferring Tree-sitter over regex."""
    if ts_result is None:
        ts_result = extract_from_file(source_file.path, source_file.language, content)
    if ts_result.used_treesitter:
        return [
            ImportInfo(
                source_file=source_file.relative_path,
                imported_path=imp.path,
                is_relative=imp.is_relative,
                is_external=_is_external_import(imp.path),
            )
            for imp in ts_result.imports
        ]

    # Regex fallback
    patterns = IMPORT_PATTERNS.get(source_file.language, [])
    imports: list[ImportInfo] = []
    for pattern in patterns:
        for match in pattern.finditer(content):
            imported = match.group(1)
            is_relative = imported.startswith(".")
            imports.append(
                ImportInfo(
                    source_file=source_file.relative_path,
                    imported_path=imported,
                    is_relative=is_relative,
                    is_external=_is_external_import(imported),
                )
            )
    return imports


def _extract_classes(
    content: str,
    source_file: SourceFile,
    ts_result: TSExtractionResult | None = None,
) -> list[ClassInfo]:
    """Extract class definitions, preferring Tree-sitter over regex."""
    if ts_result is None:
        ts_result = extract_from_file(source_file.path, source_file.language, content)
    if ts_result.used_treesitter:
        return [
            ClassInfo(
                name=cls.name,
                file_path=source_file.relative_path,
                parent_classes=cls.bases,
                line_number=cls.line,
            )
            for cls in ts_result.classes
        ]

    # Regex fallback
    patterns = CLASS_PATTERNS.get(source_file.language, [])
    classes: list[ClassInfo] = []
    for pattern in patterns:
        for match in pattern.finditer(content):
            name = match.group(1)
            parents: list[str] = []
            if match.lastindex and match.lastindex >= 2 and match.group(2):
                parents = [p.strip() for p in match.group(2).split(",") if p.strip()]
            line_num = content[: match.start()].count("\n") + 1
            classes.append(
                ClassInfo(
                    name=name,
                    file_path=source_file.relative_path,
                    parent_classes=parents,
                    line_number=line_num,
                )
            )
    return classes


def _extract_functions(
    content: str,
    source_file: SourceFile,
    ts_result: TSExtractionResult | None = None,
) -> list[FunctionInfo]:
    """Extract function definitions, preferring Tree-sitter over regex."""
    if ts_result is None:
        ts_result = extract_from_file(source_file.path, source_file.language, content)
    if ts_result.used_treesitter:
        return [
            FunctionInfo(
                name=fn.name,
                file_path=source_file.relative_path,
                line_number=fn.line,
            )
            for fn in ts_result.functions
        ]

    # Regex fallback
    patterns = FUNCTION_PATTERNS.get(source_file.language, [])
    functions: list[FunctionInfo] = []
    for pattern in patterns:
        for match in pattern.finditer(content):
            name = match.group(1)
            if name.startswith("__") and name.endswith("__"):
                continue
            line_num = content[: match.start()].count("\n") + 1
            functions.append(
                FunctionInfo(
                    name=name,
                    file_path=source_file.relative_path,
                    line_number=line_num,
                )
            )
    return functions


#: Languages whose imports and classes ArchiLens reads. Files in any other language are
#: still grouped into modules, but those modules get no dependencies or components.
ANALYZED_LANGUAGES = frozenset(IMPORT_PATTERNS) | SCRIPT_HOSTS


def unanalyzed_languages(files: list[SourceFile]) -> dict[str, int]:
    """Language name -> count of non-test files in languages outside ``ANALYZED_LANGUAGES``."""
    counts: dict[str, int] = {}
    for f in files:
        if not f.is_test and f.language not in ANALYZED_LANGUAGES:
            name = LANGUAGE_NAMES.get(f.language, f.language)
            counts[name] = counts.get(name, 0) + 1
    return counts


def _is_external_import(path: str) -> bool:
    """Heuristic: path looks like a third-party package rather than a local module."""
    return not path.startswith(".") and "/" not in path and not path.startswith("src")


def _humanize_module_name(module_path: str) -> str:
    """Convert module path to human-readable name."""
    if module_path == ROOT_MODULE:
        return "Root"
    words = module_path.split("/")[-1].replace("_", " ").replace("-", " ").split()
    # Capitalise each word's first letter only: str.title() would turn "ginS" into "Gins" and "API" into "Api".
    return " ".join(word[:1].upper() + word[1:] for word in words)

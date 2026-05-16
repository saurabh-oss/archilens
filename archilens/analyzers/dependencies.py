"""
Dependency analyzer using Tree-sitter for multi-language AST parsing.

Extracts import statements, function calls, class hierarchies,
and other structural relationships from source code to build
the architecture dependency graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import networkx as nx

from archilens.analyzers.discovery import Module, SourceFile
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

import re

# Patterns for extracting imports across languages
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
    source_file: str      # File containing the import
    imported_path: str     # What was imported (module path or package)
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


@dataclass
class FunctionInfo:
    """An extracted function/method definition."""
    name: str
    file_path: str
    line_number: int = 0
    is_method: bool = False
    parent_class: Optional[str] = None


def analyze_dependencies(
    files: list[SourceFile],
    modules: dict[str, Module],
    repo_path: str | Path,
) -> tuple[list[ArchNode], list[ArchEdge], nx.DiGraph]:
    """
    Analyze all source files and build the architecture graph.
    
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
            continue
        
        try:
            content = source_file.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            logger.warning(f"Cannot read {source_file.relative_path}")
            continue
        
        # Extract imports
        imports = _extract_imports(content, source_file)
        all_imports.extend(imports)
        
        # Extract classes
        classes = _extract_classes(content, source_file)
        all_classes.extend(classes)
        
        # Extract functions
        functions = _extract_functions(content, source_file)
        all_functions.extend(functions)
    
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
    for cls in all_classes:
        module_name = _file_to_module(cls.file_path)
        parent_id = f"module:{module_name}"
        
        node = ArchNode(
            id=f"class:{cls.file_path}:{cls.name}",
            name=cls.name,
            node_type=NodeType.CLASS,
            level=DiagramLevel.COMPONENT,
            file_path=cls.file_path,
            line_start=cls.line_number,
            parent=parent_id,
        )
        nodes.append(node)
        
        # Register as child of parent module
        for n in nodes:
            if n.id == parent_id:
                n.children.append(node.id)
                break
    
    # Phase 4: Resolve imports into edges
    edges: list[ArchEdge] = []
    module_names = set(modules.keys())
    
    # Build a lookup: file path -> module name
    file_to_module_map: dict[str, str] = {}
    for f in files:
        file_to_module_map[f.relative_path] = f.module
    
    # Aggregate imports at module level
    module_deps: dict[tuple[str, str], int] = {}
    
    for imp in all_imports:
        source_module = file_to_module_map.get(imp.source_file, "<unknown>")
        target_module = _resolve_import_to_module(
            imp.imported_path, source_module, module_names, imp.is_relative
        )
        
        if target_module and target_module != source_module:
            key = (source_module, target_module)
            module_deps[key] = module_deps.get(key, 0) + 1
    
    for (src, tgt), weight in module_deps.items():
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
    
    # Detect cycles (architectural smell)
    try:
        cycles = list(nx.simple_cycles(graph))
        for cycle in cycles:
            for node_id in cycle:
                metrics.setdefault(node_id, {})["in_cycle"] = 1.0
    except Exception:
        pass
    
    return metrics


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _extract_imports(content: str, source_file: SourceFile) -> list[ImportInfo]:
    """Extract import statements, preferring Tree-sitter over regex."""
    from archilens.analyzers.treesitter import extract_from_file

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
            imports.append(ImportInfo(
                source_file=source_file.relative_path,
                imported_path=imported,
                is_relative=is_relative,
                is_external=_is_external_import(imported),
            ))
    return imports


def _extract_classes(content: str, source_file: SourceFile) -> list[ClassInfo]:
    """Extract class definitions, preferring Tree-sitter over regex."""
    from archilens.analyzers.treesitter import extract_from_file

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
            line_num = content[:match.start()].count("\n") + 1
            classes.append(ClassInfo(
                name=name,
                file_path=source_file.relative_path,
                parent_classes=parents,
                line_number=line_num,
            ))
    return classes


def _extract_functions(content: str, source_file: SourceFile) -> list[FunctionInfo]:
    """Extract function definitions, preferring Tree-sitter over regex."""
    from archilens.analyzers.treesitter import extract_from_file

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
            line_num = content[:match.start()].count("\n") + 1
            functions.append(FunctionInfo(
                name=name,
                file_path=source_file.relative_path,
                line_number=line_num,
            ))
    return functions


def _is_external_import(path: str) -> bool:
    """Heuristic: path looks like a third-party package rather than a local module."""
    return (
        not path.startswith(".")
        and "/" not in path
        and not path.startswith("src")
    )


def _resolve_import_to_module(
    imported_path: str,
    source_module: str,
    known_modules: set[str],
    is_relative: bool,
) -> Optional[str]:
    """Resolve an import path to a known module name."""
    # For relative imports, resolve against source module
    if is_relative:
        parts = source_module.split("/")
        imported_parts = imported_path.lstrip(".").split(".")
        resolved = "/".join(parts[:-1] + imported_parts[:2])
        if resolved in known_modules:
            return resolved
    
    # Try direct match: "src.orders.service" -> "src/orders"
    parts = imported_path.replace(".", "/").split("/")
    for depth in [2, 1]:
        candidate = "/".join(parts[:depth])
        if candidate in known_modules:
            return candidate
    
    return None


def _file_to_module(file_path: str) -> str:
    """Convert file path to module name using same logic as discovery."""
    parts = Path(file_path).parts
    if len(parts) <= 1:
        return "<root>"
    module_depth = min(2, len(parts) - 1)
    return "/".join(parts[:module_depth])


def _humanize_module_name(module_path: str) -> str:
    """Convert module path to human-readable name."""
    if module_path == "<root>":
        return "Root"
    parts = module_path.split("/")
    # Take the last meaningful part and title-case it
    name = parts[-1].replace("_", " ").replace("-", " ").title()
    return name

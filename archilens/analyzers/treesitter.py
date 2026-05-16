"""
Tree-sitter AST-based code extraction for accurate, language-aware parsing.

Used as the primary extraction backend in dependencies.py, falling back
to regex patterns for languages without a tree-sitter grammar or when the
tree-sitter packages are not installed.

Supports tree-sitter >= 0.24 (QueryCursor-based API).
Supported languages: Python, JavaScript, TypeScript, Java, Go.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grammar registry — lazy-loaded per language
# ---------------------------------------------------------------------------

_LANGUAGES: dict[str, Any] = {}   # language_name -> Language object
_PARSERS: dict[str, Any] = {}     # language_name -> Parser object


def _get_lang_parser(language: str) -> Optional[tuple[Any, Any]]:
    """Return (Language, Parser) for *language*, or None if unavailable."""
    if language in _PARSERS:
        return _LANGUAGES[language], _PARSERS[language]

    try:
        from tree_sitter import Language, Parser  # type: ignore[import]

        lang_obj: Any

        if language == "python":
            import tree_sitter_python as _m  # type: ignore[import]
            lang_obj = Language(_m.language())

        elif language == "javascript":
            import tree_sitter_javascript as _m  # type: ignore[import]
            lang_obj = Language(_m.language())

        elif language == "typescript":
            import tree_sitter_typescript as _m  # type: ignore[import]
            fn = getattr(_m, "language_typescript", None) or getattr(_m, "language", None)
            if fn is None:
                return None
            lang_obj = Language(fn())

        elif language == "java":
            import tree_sitter_java as _m  # type: ignore[import]
            lang_obj = Language(_m.language())

        elif language == "go":
            import tree_sitter_go as _m  # type: ignore[import]
            lang_obj = Language(_m.language())

        else:
            return None

        parser = Parser(lang_obj)
        _LANGUAGES[language] = lang_obj
        _PARSERS[language] = parser
        return lang_obj, parser

    except (ImportError, AttributeError, Exception) as exc:
        logger.debug("Tree-sitter unavailable for %s: %s", language, exc)
        return None


# ---------------------------------------------------------------------------
# Query strings per language
# ---------------------------------------------------------------------------

# We capture the full import statement node and post-process its text with
# the existing IMPORT_PATTERNS regex — this lets tree-sitter filter out false
# positives (imports inside strings / block comments) while keeping regex for
# the actual path extraction.

_IMPORT_QUERIES: dict[str, str] = {
    "python": """
        [ (import_statement) @stmt
          (import_from_statement) @stmt ]
    """,
    "javascript": """
        [ (import_statement) @stmt
          (call_expression
            function: (identifier) @_fn
            (#eq? @_fn "require")) @stmt ]
    """,
    "typescript": """
        (import_statement) @stmt
    """,
    "java": """
        (import_declaration) @stmt
    """,
    "go": """
        [ (import_declaration) @stmt
          (import_spec) @stmt ]
    """,
}

_CLASS_QUERIES: dict[str, str] = {
    "python": """
        (class_definition name: (identifier) @class_name)
    """,
    "javascript": """
        (class_declaration name: (identifier) @class_name)
    """,
    "typescript": """
        [ (class_declaration name: (type_identifier) @class_name)
          (interface_declaration name: (type_identifier) @class_name) ]
    """,
    "java": """
        [ (class_declaration name: (identifier) @class_name)
          (interface_declaration name: (identifier) @class_name)
          (enum_declaration name: (identifier) @class_name) ]
    """,
    "go": """
        (type_declaration (type_spec name: (type_identifier) @class_name))
    """,
}

_FUNCTION_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @func_name)
    """,
    "javascript": """
        [ (function_declaration name: (identifier) @func_name)
          (method_definition name: (property_identifier) @func_name) ]
    """,
    "typescript": """
        [ (function_declaration name: (identifier) @func_name)
          (method_definition name: (property_identifier) @func_name)
          (method_signature name: (property_identifier) @func_name) ]
    """,
    "java": """
        (method_declaration name: (identifier) @func_name)
    """,
    "go": """
        [ (function_declaration name: (identifier) @func_name)
          (method_declaration name: (field_identifier) @func_name) ]
    """,
}

# Python-specific: extract base class names alongside the class name
_PYTHON_BASES_QUERY = """
    (class_definition
      name: (identifier) @class_name
      superclasses: (argument_list (identifier) @base))
"""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TSImport:
    path: str
    is_relative: bool = False


@dataclass
class TSClass:
    name: str
    line: int
    bases: list[str] = field(default_factory=list)


@dataclass
class TSFunction:
    name: str
    line: int


@dataclass
class TSExtractionResult:
    imports: list[TSImport] = field(default_factory=list)
    classes: list[TSClass] = field(default_factory=list)
    functions: list[TSFunction] = field(default_factory=list)
    used_treesitter: bool = False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_from_file(
    file_path: Path,
    language: str,
    source: str,
) -> TSExtractionResult:
    """
    Extract imports, classes, and functions using Tree-sitter AST parsing.

    Returns a result with ``used_treesitter=False`` when tree-sitter is
    unavailable for *language*; the caller should then fall back to regex.
    """
    result = TSExtractionResult()
    pair = _get_lang_parser(language)
    if pair is None:
        return result

    lang_obj, parser = pair
    source_bytes = source.encode("utf-8", errors="replace")

    try:
        tree = parser.parse(source_bytes)
    except Exception as exc:
        logger.debug("Tree-sitter parse error in %s: %s", file_path, exc)
        return result

    result.used_treesitter = True

    # --- Imports -------------------------------------------------------
    import_query_str = _IMPORT_QUERIES.get(language)
    if import_query_str:
        try:
            captures = _query_captures(lang_obj, import_query_str, tree.root_node)
            for node in captures.get("stmt", []):
                text = source_bytes[node.start_byte:node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                result.imports.extend(_parse_import_text(text, language))
        except Exception as exc:
            logger.debug("Import query failed for %s (%s): %s", file_path, language, exc)

    # --- Classes -------------------------------------------------------
    class_query_str = _CLASS_QUERIES.get(language)
    if class_query_str:
        try:
            captures = _query_captures(lang_obj, class_query_str, tree.root_node)
            seen: set[str] = set()
            for node in captures.get("class_name", []):
                name = _node_text(node, source_bytes)
                if name and name not in seen:
                    seen.add(name)
                    result.classes.append(TSClass(name=name, line=node.start_point[0] + 1))
        except Exception as exc:
            logger.debug("Class query failed for %s (%s): %s", file_path, language, exc)

    # Python: enrich with superclass names
    if language == "python" and result.classes:
        try:
            bases_map: dict[str, list[str]] = {}
            for _, cap_dict in _query_matches(lang_obj, _PYTHON_BASES_QUERY, tree.root_node):
                cn_nodes = cap_dict.get("class_name", [])
                base_nodes = cap_dict.get("base", [])
                if cn_nodes:
                    cn = _node_text(cn_nodes[0], source_bytes)
                    bases = [_node_text(b, source_bytes) for b in base_nodes]
                    bases_map.setdefault(cn, []).extend(bases)
            for cls in result.classes:
                if cls.name in bases_map:
                    cls.bases = list(dict.fromkeys(bases_map[cls.name]))
        except Exception as exc:
            logger.debug("Base class query failed for %s: %s", file_path, exc)

    # --- Functions -----------------------------------------------------
    func_query_str = _FUNCTION_QUERIES.get(language)
    if func_query_str:
        try:
            captures = _query_captures(lang_obj, func_query_str, tree.root_node)
            seen_fn: set[tuple[str, int]] = set()
            for node in captures.get("func_name", []):
                name = _node_text(node, source_bytes)
                if not name:
                    continue
                # Skip dunder methods
                if name.startswith("__") and name.endswith("__"):
                    continue
                line = node.start_point[0] + 1
                key = (name, line)
                if key not in seen_fn:
                    seen_fn.add(key)
                    result.functions.append(TSFunction(name=name, line=line))
        except Exception as exc:
            logger.debug("Function query failed for %s (%s): %s", file_path, language, exc)

    return result


# ---------------------------------------------------------------------------
# Query execution helpers — tree-sitter >= 0.24 (QueryCursor API)
# ---------------------------------------------------------------------------

def _query_captures(lang_obj: Any, query_str: str, node: Any) -> dict[str, list[Any]]:
    """
    Execute a query and return captures as ``dict[capture_name, list[Node]]``.

    Uses the tree-sitter 0.24+ ``Query`` + ``QueryCursor`` API.
    Falls back to the deprecated ``Language.query().captures()`` for older builds.
    """
    try:
        from tree_sitter import Query, QueryCursor  # type: ignore[import]
        q = Query(lang_obj, query_str)
        cursor = QueryCursor(q)
        result = cursor.captures(node)
        if isinstance(result, dict):
            return result
        # Older list-of-tuples shape
        out: dict[str, list[Any]] = {}
        for n, name in result:
            out.setdefault(name, []).append(n)
        return out
    except Exception:
        pass

    # Last-resort: deprecated lang.query() path
    try:
        q = lang_obj.query(query_str)
        raw = q.captures(node)
        if isinstance(raw, dict):
            return raw
        out = {}
        for n, name in raw:
            out.setdefault(name, []).append(n)
        return out
    except Exception:
        return {}


def _query_matches(
    lang_obj: Any,
    query_str: str,
    node: Any,
) -> list[tuple[int, dict[str, list[Any]]]]:
    """
    Execute a query and return matches as a list of (pattern_idx, capture_dict).
    """
    try:
        from tree_sitter import Query, QueryCursor  # type: ignore[import]
        q = Query(lang_obj, query_str)
        cursor = QueryCursor(q)
        raw = cursor.matches(node)
        out = []
        for item in raw:
            if isinstance(item, tuple) and len(item) == 2:
                idx, caps = item
                if isinstance(caps, dict):
                    normalised = {
                        k: (v if isinstance(v, list) else [v])
                        for k, v in caps.items()
                    }
                    out.append((idx, normalised))
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_text(node: Any, source_bytes: bytes) -> str:
    """Decode the source span covered by *node*."""
    try:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


# Reuse the same pattern set as dependencies.py applied to tree-sitter-captured
# import statement text (avoids false positives from comments / strings).
_IMPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE),
        re.compile(r"^\s*from\s+([\w.]+)\s+import", re.MULTILINE),
        re.compile(r"^\s*from\s+(\.+[\w.]*)\s+import", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"""from\s+['"]([^'"]+)['"]"""),
        re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    ],
    "typescript": [
        re.compile(r"""from\s+['"]([^'"]+)['"]"""),
    ],
    "java": [
        re.compile(r"import\s+([\w.]+)(?:\s*;)?"),
    ],
    "go": [
        re.compile(r'"([\w./\-]+)"'),
    ],
}


def _parse_import_text(text: str, language: str) -> list[TSImport]:
    """Extract import paths from a single import statement's source text."""
    patterns = _IMPORT_PATTERNS.get(language, [])
    results: list[TSImport] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in pat.finditer(text):
            path = m.group(1).strip()
            if path and path not in seen:
                seen.add(path)
                results.append(TSImport(path=path, is_relative=path.startswith(".")))
    return results

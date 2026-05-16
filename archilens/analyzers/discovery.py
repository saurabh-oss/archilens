"""
File discovery and language detection.

Walks the repository tree, applies exclusion filters from config,
auto-detects programming languages, and groups files into logical
modules based on directory structure.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

from archilens.config import ArchiLensConfig

logger = logging.getLogger(__name__)

# Hard cap: warn above this, stop collecting above the hard limit.
_WARN_FILE_COUNT = 5_000
_MAX_FILE_COUNT = 20_000

# Mapping: file extension -> language name
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
}

# Files that indicate module boundaries
MODULE_MARKERS = {
    "python": ["__init__.py", "setup.py", "pyproject.toml"],
    "javascript": ["package.json", "index.js", "index.ts"],
    "typescript": ["package.json", "index.ts", "index.js"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
}


@dataclass
class SourceFile:
    """A discovered source file."""

    path: Path
    relative_path: str
    language: str
    module: str  # Logical module this file belongs to
    lines: int = 0
    is_entry_point: bool = False
    is_test: bool = False


@dataclass
class Module:
    """A logical module (directory-based grouping)."""

    name: str
    path: str
    files: list[SourceFile] = field(default_factory=list)
    total_lines: int = 0
    languages: set[str] = field(default_factory=set)
    sub_modules: list[str] = field(default_factory=list)


def discover_files(
    repo_path: str | Path,
    config: ArchiLensConfig,
) -> tuple[list[SourceFile], dict[str, Module]]:
    """
    Discover all source files in the repository and organize them into modules.

    Returns:
        Tuple of (list of all source files, dict of module name -> Module)
    """
    repo_path = Path(repo_path).resolve()
    exclude_patterns = config.analysis.exclude
    allowed_languages = set(config.analysis.languages) if config.analysis.languages else None

    files: list[SourceFile] = []
    modules: dict[str, Module] = {}

    for file_path in repo_path.rglob("*"):
        if not file_path.is_file():
            continue

        relative = str(file_path.relative_to(repo_path))

        # Apply exclusion filters
        if _is_excluded(relative, exclude_patterns):
            continue

        # Detect language from extension
        language = EXTENSION_MAP.get(file_path.suffix.lower())
        if language is None:
            continue

        # Filter by configured languages
        if allowed_languages and language not in allowed_languages:
            continue

        # Count lines
        try:
            line_count = sum(1 for _ in file_path.open(encoding="utf-8", errors="ignore"))
        except OSError:
            line_count = 0

        # Determine module from directory structure
        module_name = _determine_module(relative, repo_path)

        # Check if test file
        is_test = _is_test_file(relative)

        # Check if entry point
        is_entry = _is_entry_point(relative, config.analysis.entry_points)

        source_file = SourceFile(
            path=file_path,
            relative_path=relative,
            language=language,
            module=module_name,
            lines=line_count,
            is_entry_point=is_entry,
            is_test=is_test,
        )
        files.append(source_file)

        if len(files) == _WARN_FILE_COUNT:
            logger.warning(
                "Discovered %d source files — this is a large repo. "
                "Analysis may be slow. Use 'analysis.exclude' in .archilens.yml "
                "to narrow scope, or '--level 1' to skip component-level detail.",
                _WARN_FILE_COUNT,
            )
        if len(files) >= _MAX_FILE_COUNT:
            logger.warning(
                "Reached the %d file limit — stopping discovery. "
                "Results will be partial. Add more paths to 'analysis.exclude'.",
                _MAX_FILE_COUNT,
            )
            break

        # Build module registry
        if module_name not in modules:
            modules[module_name] = Module(name=module_name, path=module_name)
        mod = modules[module_name]
        mod.files.append(source_file)
        mod.total_lines += line_count
        mod.languages.add(language)

    # Resolve sub-module relationships
    _resolve_submodules(modules)

    return files, modules


def _is_excluded(relative_path: str, patterns: list[str]) -> bool:
    """Check if a file matches any exclusion pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(relative_path, pattern):
            return True
        # Also check each path segment
        if fnmatch.fnmatch(f"/{relative_path}", pattern):
            return True
    return False


def _determine_module(relative_path: str, repo_root: Path) -> str:
    """
    Determine the logical module a file belongs to.

    Uses the first two directory levels as the module identifier.
    For flat structures, uses the first directory level.
    """
    parts = Path(relative_path).parts

    if len(parts) <= 1:
        return "<root>"

    # Use up to 2 directory levels for module grouping
    # e.g., "src/orders/service.py" -> "src/orders"
    # e.g., "lib/utils.py" -> "lib"
    module_depth = min(2, len(parts) - 1)
    return "/".join(parts[:module_depth])


def _is_test_file(relative_path: str) -> bool:
    """Detect test files by naming convention."""
    name = Path(relative_path).stem.lower()
    test_indicators = ["test_", "_test", "tests", "spec_", "_spec", "specs"]
    path_lower = relative_path.lower()
    return (
        any(name.startswith(t) or name.endswith(t) for t in test_indicators)
        or "/tests/" in path_lower
        or "/test/" in path_lower
        or "/__tests__/" in path_lower
    )


def _is_entry_point(relative_path: str, patterns: list[dict[str, str]]) -> bool:
    """Check if a file matches any configured entry point pattern."""
    for entry in patterns:
        pattern = entry.get("pattern", "")
        if fnmatch.fnmatch(relative_path, pattern):
            return True
    return False


def _resolve_submodules(modules: dict[str, Module]) -> None:
    """Build parent-child relationships between modules."""
    module_names = sorted(modules.keys())
    for name in module_names:
        parts = name.split("/")
        if len(parts) > 1:
            parent = parts[0]
            if parent in modules and parent != name:
                modules[parent].sub_modules.append(name)

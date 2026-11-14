"""
File discovery and language detection.

Walks the repository tree, applies exclusion filters from config,
auto-detects programming languages, and groups files into logical
modules based on directory structure.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from archilens.analyzers.entry_points import detect_entry_points
from archilens.analyzers.layout import ModuleLayout
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
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".vue": "vue",
    ".svelte": "svelte",
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
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
}

#: How each language is written in messages to the user.
LANGUAGE_NAMES: dict[str, str] = {
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "go": "Go",
    "java": "Java",
    "javascript": "JavaScript",
    "kotlin": "Kotlin",
    "php": "PHP",
    "python": "Python",
    "ruby": "Ruby",
    "rust": "Rust",
    "scala": "Scala",
    "svelte": "Svelte",
    "swift": "Swift",
    "typescript": "TypeScript",
    "vue": "Vue",
}

# Directories never worth walking when Git can't tell us what is ignored.
_PRUNED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".gradle",
    ".next",
}

# A file anywhere below one of these directories is a test / fixture file.
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testdata", "e2e"}

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
    entry_type: str = ""  # configured or detected entry-point type, when is_entry_point
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
    all_paths = _candidate_paths(repo_path)

    for relative in all_paths:
        file_path = repo_path / relative
        if not file_path.is_file():
            continue

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

        # Check if test file
        is_test = _is_test_file(relative)

        # Check if entry point
        entry_type = entry_point_type(relative, config.analysis.entry_points)

        source_file = SourceFile(
            path=file_path,
            relative_path=relative,
            language=language,
            module="",  # assigned below, once the repository layout is known
            lines=line_count,
            is_entry_point=entry_type is not None,
            entry_type=entry_type or "",
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

    # With no configured entry points, detect them from manifests and file names.
    if not config.analysis.entry_points:
        detected = detect_entry_points(repo_path, all_paths, [f.relative_path for f in files if not f.is_test])
        for source_file in files:
            if source_file.relative_path in detected:
                source_file.is_entry_point = True
                source_file.entry_type = detected[source_file.relative_path]

    # Group files into modules using source roots (src/, src/main/java, go.mod,
    # workspace packages) rather than a fixed number of leading directories.
    layout = ModuleLayout.build(
        (f.relative_path for f in files if not f.is_test),
        all_paths,
        depth=config.analysis.module_depth,
    )
    for source_file in files:
        source_file.module = layout.module_for(source_file.relative_path)
        if source_file.module not in modules:
            modules[source_file.module] = Module(name=source_file.module, path=source_file.module)
        mod = modules[source_file.module]
        mod.files.append(source_file)
        mod.total_lines += source_file.lines
        mod.languages.add(source_file.language)

    # Resolve sub-module relationships
    _resolve_submodules(modules)

    return files, modules


def _candidate_paths(repo_path: Path) -> list[str]:
    """
    List candidate files as sorted, POSIX-style paths relative to *repo_path*.

    Inside a Git work tree this is ``git ls-files``: tracked files plus untracked
    files that are not ignored, so ``.gitignore`` is respected and virtualenvs,
    build output and dependency folders are skipped without configuration.
    Elsewhere (no Git, or a ref extracted to a temp dir) the tree is walked,
    pruning well-known dependency and build directories.

    Paths are POSIX-style on every OS so node IDs, diagrams and diffs are
    identical on Windows and Linux.
    """
    listed = _git_ls_files(repo_path)
    if listed is not None:
        return sorted(listed)

    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = sorted(d for d in dirnames if d not in _PRUNED_DIRS)
        rel_dir = Path(dirpath).relative_to(repo_path).as_posix()
        for name in sorted(filenames):
            found.append(name if rel_dir == "." else f"{rel_dir}/{name}")
    return found


def _git_ls_files(repo_path: Path) -> list[str] | None:
    """Return ``git ls-files`` output for *repo_path*, or None if Git can't be used."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None  # not a work tree, or git failed
    names = proc.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [n for n in names if n]


def _is_excluded(relative_path: str, patterns: list[str]) -> bool:
    """Check if a file matches any exclusion pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(relative_path, pattern):
            return True
        # Also check each path segment
        if fnmatch.fnmatch(f"/{relative_path}", pattern):
            return True
    return False


def _is_test_file(relative_path: str) -> bool:
    """Detect test files by naming convention or by living in a test directory."""
    parts = Path(relative_path.replace("\\", "/").lower()).parts
    name = Path(parts[-1]).stem if parts else ""
    test_indicators = ["test_", "_test", "tests", "spec_", "_spec", "specs"]
    return any(name.startswith(t) or name.endswith(t) for t in test_indicators) or any(
        d in _TEST_DIRS for d in parts[:-1]
    )


def entry_point_type(relative_path: str, patterns: list[dict[str, str]]) -> str | None:
    """The ``type`` of the first configured entry point matching the file, "" if untyped, or None."""
    for entry in patterns:
        if fnmatch.fnmatch(relative_path, entry.get("pattern", "")):
            return entry.get("type", "")
    return None


def _resolve_submodules(modules: dict[str, Module]) -> None:
    """Build parent-child relationships between modules."""
    module_names = sorted(modules.keys())
    for name in module_names:
        parts = name.split("/")
        if len(parts) > 1:
            parent = parts[0]
            if parent in modules and parent != name:
                modules[parent].sub_modules.append(name)

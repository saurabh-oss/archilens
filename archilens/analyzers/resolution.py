"""
Resolve import statements to files in the repository, per language.

Each resolver turns the text of an import (``app.orders.models``, ``../router``,
``@nestjs/common``, ``github.com/acme/shop/internal/payments``,
``com.acme.payments.Gateway``) into the repository-relative path of the file it
refers to, or None for third-party and standard-library imports. Module-level
edges are then built from the files' modules.

Supported:

- **Python**: absolute imports against every source root (repository root and
  the parents of top-level packages, such as ``src``), and relative imports
  (``.x``, ``..x``).
- **JavaScript / TypeScript**: relative specifiers with extension and
  ``index`` probing (including ``.js`` specifiers that point at ``.ts``
  sources), ``tsconfig.json`` / ``jsconfig.json`` ``baseUrl`` and ``paths``
  aliases, and workspace packages by their ``package.json`` name.
- **Java**: fully qualified class imports, wildcard package imports and static
  imports, using each file's ``package`` declaration.
- **Go**: import paths under any ``go.mod`` module in the repository.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Component files come last: `import Cart from './Cart'` means Cart.ts before Cart.vue.
_JS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
_JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
_JS_APP_MARKERS = frozenset({"package.json", "svelte.config.js", "svelte.config.ts", "svelte.config.mjs"})
_JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


@dataclass
class _TsConfig:
    directory: str
    base_url: str | None  # repo-relative directory, or None
    paths: list[tuple[str, list[str]]] = field(default_factory=list)  # (pattern, targets relative to repo)


class ImportResolver:
    """Resolve imports to repository files. Build once per analysis."""

    def __init__(self, repo_path: Path, source_paths: Iterable[str], all_paths: Iterable[str]) -> None:
        self.repo_path = repo_path
        self.files: set[str] = set(source_paths)
        self.dirs: dict[str, list[str]] = {}
        for path in sorted(self.files):
            directory = posixpath.dirname(path)
            self.dirs.setdefault(directory, []).append(path)
            while directory:  # register ancestors so namespace packages resolve
                directory = posixpath.dirname(directory)
                self.dirs.setdefault(directory, [])

        others = [p for p in all_paths if "node_modules" not in p.split("/")]
        self.python_roots = self._python_roots()
        self.js_packages = self._js_packages(p for p in others if posixpath.basename(p) == "package.json")
        self.tsconfigs = self._tsconfigs(
            p for p in others if posixpath.basename(p) in ("tsconfig.json", "jsconfig.json")
        )
        # Directories that root a JS app, deepest first: where $lib, @/ and ~/ are relative to.
        self.js_roots = sorted(
            {posixpath.dirname(p) for p in others if posixpath.basename(p) in _JS_APP_MARKERS}, key=len, reverse=True
        )
        self.go_modules = self._go_modules(p for p in others if posixpath.basename(p) == "go.mod")
        self.java_classes, self.java_packages = self._java_index()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def resolve(self, imported: str, language: str, source_path: str) -> str | None:
        """Return the repository file *imported* refers to, or None if it is external/unknown."""
        try:
            if language == "python":
                return self._python(imported, source_path)
            if language in ("javascript", "typescript", "vue", "svelte"):
                return self._js(imported, source_path)
            if language == "java":
                return self._java(imported)
            if language == "go":
                return self._go(imported)
        except Exception as exc:  # resolution must never break the analysis
            logger.debug("Could not resolve %r from %s: %s", imported, source_path, exc)
        return None

    # ------------------------------------------------------------------
    # Python
    # ------------------------------------------------------------------

    def _python_roots(self) -> list[str]:
        packages = {posixpath.dirname(p) for p in self.files if posixpath.basename(p) == "__init__.py"}
        roots = {""}
        for package in packages:
            parent = posixpath.dirname(package)
            if package and parent not in packages:
                roots.add(parent)
        if "src" in self.dirs:
            roots.add("src")  # src layout with namespace (no __init__.py) packages
        return sorted(roots, key=lambda r: (r != "", r))  # repository root first

    def _python_roots_for(self, source_path: str) -> list[str]:
        """
        Roots an absolute import in *source_path* is resolved against, approximating
        its ``sys.path``: the file's own source root, the repository root, and
        ``src`` (an installed src-layout package is importable from anywhere).
        Other roots, such as a ``docs_src`` folder of examples, are not on other
        files' path; trying them would let a local ``opentelemetry`` example shadow
        the real third-party package.
        """
        own = next(
            (r for r in sorted(self.python_roots, key=len, reverse=True) if r and source_path.startswith(r + "/")), ""
        )
        return list(dict.fromkeys(r for r in (own, "", "src") if r in self.python_roots))

    def _python(self, imported: str, source_path: str) -> str | None:
        if imported.startswith("."):
            level = len(imported) - len(imported.lstrip("."))
            base = posixpath.dirname(source_path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            rest = imported[level:]
            return self._python_module(base, rest.split(".") if rest else [])

        parts = imported.split(".")
        for root in self._python_roots_for(source_path):
            for k in range(len(parts), 0, -1):  # longest prefix: "a.b.C" may name a class in a/b.py
                found = self._python_module(root, parts[:k])
                if found:
                    return found
        return None

    def _python_module(self, base: str, parts: list[str]) -> str | None:
        stem = posixpath.join(base, *parts) if parts else base
        for candidate in (f"{stem}.py", posixpath.join(stem, "__init__.py") if stem else "__init__.py"):
            if candidate in self.files:
                return candidate
        if parts and stem in self.dirs:  # namespace package
            return self._first_file(stem, (".py",))
        return None

    # ------------------------------------------------------------------
    # JavaScript / TypeScript
    # ------------------------------------------------------------------

    def _js_packages(self, manifests: Iterable[str]) -> dict[str, tuple[str, list[str]]]:
        """package name -> (directory, entry-point candidates)."""
        packages: dict[str, tuple[str, list[str]]] = {}
        for manifest in manifests:
            data = self._read_json(manifest)
            name = data.get("name") if isinstance(data, dict) else None
            if not isinstance(name, str):
                continue
            entries = [
                data[k] for k in ("source", "module", "main", "types", "typings") if isinstance(data.get(k), str)
            ]
            exports = data.get("exports")
            if isinstance(exports, str):
                entries.append(exports)
            elif isinstance(exports, dict) and isinstance(exports.get("."), str):
                entries.append(exports["."])
            packages[name] = (posixpath.dirname(manifest), entries)
        return packages

    def _tsconfigs(self, configs: Iterable[str]) -> list[_TsConfig]:
        found: list[_TsConfig] = []
        for config in configs:
            options = self._compiler_options(config)
            directory = posixpath.dirname(config)
            base: str | None = options.get("baseUrl")
            paths: list[tuple[str, list[str]]] = []
            raw_paths = options.get("paths")
            if isinstance(raw_paths, dict):
                # paths are relative to baseUrl if set, else to the config that declared them
                anchor = base if base is not None else options.get("pathsAnchor", directory)
                for pattern, targets in raw_paths.items():
                    if isinstance(targets, list):
                        paths.append(
                            (pattern, [_norm(posixpath.join(anchor, t)) for t in targets if isinstance(t, str)])
                        )
            found.append(_TsConfig(directory, base, paths))
        return sorted(found, key=lambda c: len(c.directory), reverse=True)  # nearest first

    def _compiler_options(self, config: str, depth: int = 0) -> dict[str, Any]:
        """
        Effective compilerOptions of *config*, following relative ``extends`` chains.

        ``baseUrl`` is returned repository-relative, and ``pathsAnchor`` records the
        directory of the config that declared ``paths``, because both are relative
        to the file they appear in, not to the config that inherits them.
        """
        data = self._read_json(config, allow_comments=True)
        if not isinstance(data, dict):
            return {}
        directory = posixpath.dirname(config)
        options: dict[str, Any] = {}
        parent = data.get("extends")
        if isinstance(parent, str) and parent.startswith(".") and depth < 5:
            parent_path = _norm(posixpath.join(directory, parent))
            if not parent_path.endswith(".json"):
                parent_path += ".json"
            options.update(self._compiler_options(parent_path, depth + 1))
        own = data.get("compilerOptions")
        if isinstance(own, dict):
            if isinstance(own.get("baseUrl"), str):
                options["baseUrl"] = _norm(posixpath.join(directory, own["baseUrl"]))
            if isinstance(own.get("paths"), dict):
                options["paths"] = own["paths"]
                options["pathsAnchor"] = directory
        return options

    def _js(self, spec: str, source_path: str) -> str | None:
        spec = spec.split("?", 1)[0]
        if spec.startswith("."):
            return self._probe(posixpath.normpath(posixpath.join(posixpath.dirname(source_path), spec)))
        if spec.startswith("/") or ":" in spec:  # absolute paths, URLs, node: builtins
            return None

        for config in self.tsconfigs:
            if config.directory and not source_path.startswith(config.directory + "/"):
                continue
            for pattern, targets in config.paths:
                captured = _match_wildcard(pattern, spec)
                if captured is None:
                    continue
                for target in targets:
                    found = self._probe(target.replace("*", captured))
                    if found:
                        return found
            if config.base_url is not None:
                found = self._probe(posixpath.join(config.base_url, spec) if config.base_url else spec)
                if found:
                    return found
            break  # only the nearest config applies

        # Framework conventions that no config file in the repository declares. None of these
        # can be an npm package name, and a project's own tsconfig paths were tried first.
        if spec.startswith("$"):
            # SvelteKit: $lib is src/lib of the app; $app, $env and $service-worker are virtual modules.
            if spec == "$lib" or spec.startswith("$lib/"):
                return self._probe(posixpath.join(self._app_root(source_path), "src/lib", spec[5:]).rstrip("/"))
            return None
        if spec.startswith(("@/", "~/")):
            # Vue CLI, Nuxt and many Vite setups: the app's src/ directory, or the app root itself.
            root, rest = self._app_root(source_path), spec[2:]
            return self._probe(posixpath.join(root, "src", rest)) or self._probe(posixpath.join(root, rest))

        for name in sorted(self.js_packages, key=len, reverse=True):
            if spec == name or spec.startswith(name + "/"):
                directory, entries = self.js_packages[name]
                subpath = spec[len(name) :].lstrip("/")
                if subpath:
                    return self._probe(posixpath.join(directory, subpath))
                for entry in entries:
                    found = self._probe(posixpath.normpath(posixpath.join(directory, entry)))
                    if found:
                        return found
                return self._probe(posixpath.join(directory, "index")) or self._probe(
                    posixpath.join(directory, "src", "index")
                )
        return None

    def _app_root(self, source_path: str) -> str:
        """The nearest directory above *source_path* holding a package.json or a Svelte config ('' = repo root)."""
        for root in self.js_roots:
            if not root or source_path.startswith(root + "/"):
                return root
        return ""

    def _probe(self, stem: str) -> str | None:
        """Find the source file a JS/TS specifier path refers to."""
        stem = "" if stem == "." else stem
        candidates = [stem]
        for js_ext, ts_exts in _JS_TO_TS.items():
            if stem.endswith(js_ext):
                candidates += [stem[: -len(js_ext)] + ts for ts in ts_exts]
        candidates += [stem + ext for ext in _JS_EXTENSIONS]
        candidates += [posixpath.join(stem, "index" + ext) if stem else "index" + ext for ext in _JS_EXTENSIONS]
        for candidate in candidates:
            if candidate in self.files:
                return candidate
        return None

    # ------------------------------------------------------------------
    # Java
    # ------------------------------------------------------------------

    def _java_index(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        classes: dict[str, str] = {}
        packages: dict[str, list[str]] = {}
        for path in sorted(p for p in self.files if p.endswith(".java")):
            try:
                with open(self.repo_path / path, encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(8192)
            except OSError:
                continue
            match = _JAVA_PACKAGE.search(head)
            package = match.group(1) if match else ""
            stem = posixpath.basename(path)[: -len(".java")]
            classes[f"{package}.{stem}" if package else stem] = path
            packages.setdefault(package, []).append(path)
        return classes, packages

    def _java(self, imported: str) -> str | None:
        name = imported.removesuffix(".*")
        if imported.endswith(".*") and name in self.java_packages:
            return self.java_packages[name][0]
        parts = name.split(".")
        for k in range(len(parts), 0, -1):  # strip static members and nested classes
            found = self.java_classes.get(".".join(parts[:k]))
            if found:
                return found
        return None

    # ------------------------------------------------------------------
    # Go
    # ------------------------------------------------------------------

    def _go_modules(self, manifests: Iterable[str]) -> list[tuple[str, str]]:
        modules: list[tuple[str, str]] = []
        for manifest in manifests:
            try:
                text = (self.repo_path / manifest).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = _GO_MODULE.search(text)
            if match:
                modules.append((match.group(1), posixpath.dirname(manifest)))
        return sorted(modules, key=lambda m: len(m[0]), reverse=True)

    def _go(self, imported: str) -> str | None:
        for module_path, directory in self.go_modules:
            if imported == module_path or imported.startswith(module_path + "/"):
                rel = imported[len(module_path) :].lstrip("/")
                package_dir = posixpath.join(directory, rel) if directory else rel
                return self._first_file(package_dir, (".go",), skip_suffix="_test.go")
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _first_file(self, directory: str, suffixes: tuple[str, ...], skip_suffix: str = "") -> str | None:
        for path in self.dirs.get(directory, []):
            if path.endswith(suffixes) and not (skip_suffix and path.endswith(skip_suffix)):
                return path
        return None

    def _read_json(self, path: str, allow_comments: bool = False) -> Any:
        try:
            text = (self.repo_path / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        if allow_comments:
            text = _strip_json_comments(text)
        try:
            return json.loads(text)
        except ValueError:
            return None


def _norm(path: str) -> str:
    """Normalise a repository-relative POSIX path; the repository root is ""."""
    normalised = posixpath.normpath(path) if path else ""
    return "" if normalised == "." else normalised


def _match_wildcard(pattern: str, spec: str) -> str | None:
    """Match a tsconfig ``paths`` pattern (at most one ``*``); return what ``*`` captured."""
    if "*" not in pattern:
        return "" if pattern == spec else None
    prefix, suffix = pattern.split("*", 1)
    if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
        return spec[len(prefix) : len(spec) - len(suffix)]
    return None


def _strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments and trailing commas (tsconfig files allow both)."""
    out: list[str] = []
    i, n, in_string = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))

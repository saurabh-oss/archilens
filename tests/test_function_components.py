"""
Functions as components: a file's top-level functions are one L2 component named after the
file, and imports between the files of a module are the relationships L2 draws. Before this,
code written as functions (Express, Go packages, utility modules) had no L2 diagram at all.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from archilens.analyzers.diff import compute_diff
from archilens.analyzers.evolution import analyze_evolution
from archilens.analyzers.treesitter import extract_from_file
from archilens.config import ArchiLensConfig, load_config
from archilens.engine import analyze_repository
from archilens.generators.base import MAX_MEMBERS, get_generator, l2_note, member_names, select_l2
from archilens.generators.d2 import D2Generator
from archilens.generators.mermaid import generate_component_detail
from archilens.generators.plantuml import PlantUMLGenerator
from archilens.models import ArchEdge, ArchNode, ArchSnapshot, DiagramLevel, EdgeType, NodeType


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def _analyze(repo: Path) -> ArchSnapshot:
    return analyze_repository(repo, ArchiLensConfig(), use_ai=False)


def _components(snap: ArchSnapshot) -> dict[str, ArchNode]:
    return {n.id: n for n in snap.nodes if n.level == DiagramLevel.COMPONENT}


def _imports(snap: ArchSnapshot) -> set[tuple[str, str]]:
    """Dependencies between components (never between modules)."""
    return {
        (e.source, e.target)
        for e in snap.edges
        if e.edge_type == EdgeType.DEPENDENCY and not e.source.startswith("module:")
    }


def _top_level(language: str, source: str) -> tuple[list[str], list[str]]:
    result = extract_from_file(Path("x"), language, source)
    assert result.used_treesitter
    functions = sorted(result.functions, key=lambda f: f.line)
    return [f.name for f in functions if f.top_level], [f.name for f in functions if not f.top_level]


# ---------------------------------------------------------------------------
# Which functions are a file's own
# ---------------------------------------------------------------------------


class TestTopLevelFunctions:
    def test_python_leaves_out_methods_and_nested_functions(self):
        top, rest = _top_level("python", (
            "def public(a): pass\n"
            "async def also(): pass\n"
            "class K:\n"
            "    def method(self): pass\n"
            "def outer():\n"
            "    def inner(): pass\n"
            "@decorator\n"
            "def decorated(): pass\n"
            "if FAST:\n"
            "    def conditional(): pass\n"
        ))
        assert top == ["public", "also", "outer", "decorated", "conditional"]
        assert rest == ["method", "inner"]

    def test_javascript_functions_bound_to_a_name_count(self):
        top, rest = _top_level("javascript", (
            "function decl() { function nested() {} }\n"
            "const arrow = () => {};\n"
            "export const exported = async (a) => { const innerArrow = () => 1; };\n"
            "var legacy = function () {};\n"
            "exports.viaExports = function (a) {};\n"
            "app.use = function use(fn) { var cb = function () {}; };\n"
            "function* gen() {}\n"
            "class C { method() {} }\n"
            "const config = { handler() {}, other: function () {} };\n"
            "const notAFunction = 3;\n"
        ))
        assert top == ["decl", "arrow", "exported", "legacy", "viaExports", "use", "gen"]
        assert rest == ["nested", "innerArrow", "cb", "method", "handler"]

    def test_module_exports_assignment_names_nothing(self):
        top, _ = _top_level("javascript", "module.exports = function createApp() {};\nfunction real() {}\n")
        assert top == ["real"]

    def test_typescript(self):
        top, rest = _top_level("typescript", (
            "export function typed<T>(x: T): T { return x; }\n"
            "export const handler = async (req: Request): Promise<void> => {};\n"
            "export default function Page() { return null; }\n"
            "interface Api { call(): void }\n"
            "class Service { run(): void {} }\n"
            "export const useThing = () => { function inside() {} };\n"
        ))
        assert top == ["typed", "handler", "Page", "useThing"]
        assert rest == ["call", "run", "inside"]

    def test_go_methods_belong_to_their_type(self):
        top, rest = _top_level("go", (
            "package main\n"
            "type T struct{}\n"
            "func (t *T) Method() {}\n"
            "func Exported() {}\n"
            "func unexported() { f := func() {}; f() }\n"
        ))
        assert top == ["Exported", "unexported"]
        assert rest == ["Method"]

    def test_java_has_no_functions_outside_a_class(self):
        top, rest = _top_level("java", "public class A { public void m() {} static int s() { return 1; } }\n")
        assert top == [] and rest == ["m", "s"]


# ---------------------------------------------------------------------------
# Files of functions become components
# ---------------------------------------------------------------------------

EXPRESS = {
    "lib/express.js": (
        "var proto = require('./application');\nvar req = require('./request');\n"
        "exports = module.exports = createApplication;\nfunction createApplication() {}\n"
    ),
    "lib/application.js": (
        "var compileETag = require('./utils').compileETag;\nvar app = exports = module.exports = {};\n"
        "app.init = function init() {};\napp.use = function use(fn) {};\napp.listen = function listen() {};\n"
    ),
    "lib/request.js": "var req = Object.create(null);\nreq.get = function header(name) {};\n",
    "lib/utils.js": "exports.compileETag = function (val) {};\nfunction parseExtendedQueryString(str) {}\n",
    "index.js": "module.exports = require('./lib/express');\n",
}


class TestFunctionComponents:
    def test_code_written_as_functions_has_components(self, tmp_path):
        snap = _analyze(_repo(tmp_path, EXPRESS))
        components = _components(snap)
        assert {c.name: c.members for c in components.values()} == {
            "express.js": ["createApplication"],
            "application.js": ["init", "use", "listen"],
            "request.js": ["get"],
            "utils.js": ["compileETag", "parseExtendedQueryString"],
        }
        node = components["functions:lib/application.js"]
        assert node.node_type == NodeType.FUNCTION and node.parent == "module:lib"
        assert node.file_path == "lib/application.js"
        module = next(n for n in snap.nodes if n.id == "module:lib")
        assert module.children == sorted(components)  # so the module has an L2 diagram

    def test_imports_between_files_of_a_module_are_the_relationships(self, tmp_path):
        snap = _analyze(_repo(tmp_path, EXPRESS))
        assert _imports(snap) == {
            ("functions:lib/express.js", "functions:lib/application.js"),
            ("functions:lib/express.js", "functions:lib/request.js"),
            ("functions:lib/application.js", "functions:lib/utils.js"),
        }
        # index.js -> lib/express.js crosses modules: a module dependency, as it always was
        assert [(e.source, e.target, e.weight) for e in snap.edges if e.source.startswith("module:")] == [
            ("module:<root>", "module:lib", 1)
        ]

    def test_public_functions_beside_a_class_are_a_component_private_helpers_are_not(self, tmp_path):
        snap = _analyze(_repo(tmp_path, {
            "app/core/service.py": "class Service:\n    def run(self): pass\n\ndef make_service():\n    pass\n\ndef _wire():\n    pass\n",
            "app/core/model.py": "class Model:\n    pass\n\ndef _normalise(x):\n    return x\n",
            "app/core/_compat.py": "def _text(x):\n    return x\n\ndef _bytes(x):\n    return x\n",
        }))
        assert {c.id: c.members for c in _components(snap).values()} == {
            "class:app/core/service.py:Service": [],
            "class:app/core/model.py:Model": [],
            "functions:app/core/service.py": ["make_service"],  # public only, when there are any
            "functions:app/core/_compat.py": ["_text", "_bytes"],  # a file of helpers is still a part
        }

    def test_go_exported_functions_and_package_main(self, tmp_path):
        snap = _analyze(_repo(tmp_path, {
            "go.mod": "module example.com/shop\n",
            "store/store.go": "package store\ntype Store struct{}\nfunc (s *Store) Get() {}\nfunc New() *Store { return nil }\nfunc open() {}\n",
            "store/types.go": "package store\ntype Item struct{}\nfunc validate(i Item) bool { return true }\n",
            "cmd/shop/main.go": "package main\nfunc main() { run() }\nfunc run() {}\n",
        }))
        functions = {c.id: c.members for c in _components(snap).values() if c.node_type == NodeType.FUNCTION}
        assert functions == {
            "functions:store/store.go": ["New"],
            "functions:cmd/shop/main.go": ["main", "run"],
        }

    def test_java_gets_no_function_components(self, tmp_path):
        snap = _analyze(_repo(tmp_path, {
            "src/main/java/com/shop/A.java": "package com.shop;\npublic class A { public void m() {} }\n",
        }))
        assert [c.node_type for c in _components(snap).values()] == [NodeType.CLASS]

    def test_a_vue_component_owns_its_script_functions(self, tmp_path):
        snap = _analyze(_repo(tmp_path, {
            "src/components/Cart.vue": (
                "<template><div/></template>\n<script setup>\nimport { total } from './money.js'\n"
                "function checkout() {}\nconst clear = () => {}\n</script>\n"
            ),
            "src/components/money.js": "export function total(items) { return 0 }\n",
        }))
        components = _components(snap)
        cart = components["class:src/components/Cart.vue:Cart"]
        assert cart.node_type == NodeType.COMPONENT and cart.members == ["checkout", "clear"]
        assert "functions:src/components/Cart.vue" not in components  # one box per file, not two
        assert _imports(snap) == {(cart.id, "functions:src/components/money.js")}

    def test_an_import_is_drawn_only_where_its_ends_are_not_a_guess(self, tmp_path):
        snap = _analyze(_repo(tmp_path, {
            "app/shop/__init__.py": "",
            "app/shop/models.py": "class Order:\n    pass\n\nclass Item:\n    pass\n",  # which class? unknown
            "app/shop/repo.py": "from app.shop.models import Order\n\nclass Repo:\n    pass\n",
            "app/shop/views.py": "from app.shop.repo import Repo\nfrom app.shop import views\n\ndef index():\n    pass\n",
        }))
        assert _imports(snap) == {("functions:app/shop/views.py", "class:app/shop/repo.py:Repo")}

    def test_module_level_results_do_not_change(self, tmp_path):
        snap = _analyze(_repo(tmp_path, EXPRESS))
        modules = [n for n in snap.nodes if n.node_type == NodeType.MODULE]
        assert sorted(m.id for m in modules) == ["module:<root>", "module:lib"]
        assert sum(1 for e in snap.edges if e.target.startswith("module:")) == 1


# ---------------------------------------------------------------------------
# The L2 diagram
# ---------------------------------------------------------------------------


def _module(children: list[ArchNode]) -> ArchNode:
    return ArchNode(
        id="module:lib", name="Lib", node_type=NodeType.MODULE, level=DiagramLevel.MODULE,
        file_path="lib", children=[c.id for c in children],
    )


def _functions(name: str, members: list[str]) -> ArchNode:
    return ArchNode(
        id=f"functions:lib/{name}", name=name, node_type=NodeType.FUNCTION, level=DiagramLevel.COMPONENT,
        file_path=f"lib/{name}", parent="module:lib", members=members,
    )


def _class(name: str) -> ArchNode:
    return ArchNode(
        id=f"class:lib/{name}.py:{name}", name=name, node_type=NodeType.CLASS, level=DiagramLevel.COMPONENT,
        file_path=f"lib/{name}.py", parent="module:lib",
    )


def _snapshot(children: list[ArchNode], edges: list[ArchEdge]) -> ArchSnapshot:
    return ArchSnapshot(project_name="T", git_ref="HEAD", nodes=[_module(children), *children], edges=edges)


def _imports_edge(source: ArchNode, target: ArchNode) -> ArchEdge:
    return ArchEdge(source=source.id, target=target.id, edge_type=EdgeType.DEPENDENCY, label="imports")


class TestDiagram:
    def _small(self) -> ArchSnapshot:
        utils = _functions("utils.js", ["etag", "wetag"])
        app = _functions("application.js", [f"fn{i}" for i in range(12)])
        view = _class("View")
        return _snapshot([utils, app, view], [_imports_edge(app, utils), _imports_edge(app, view)])

    def test_mermaid_lists_the_functions_and_draws_imports_dashed(self):
        text = generate_component_detail(self._small(), "module:lib")
        assert text is not None
        assert '    class C0["utils.js"] {\n        <<functions>>\n        +lib/utils.js\n        +etag()\n        +wetag()\n    }' in text
        assert "        +lib/application.js\n        12 functions, 8 listed\n        +fn0()\n" in text
        assert "        +fn7()\n    }" in text and "+fn8()" not in text
        assert "    C1 ..> C0\n    C1 ..> C2\n" in text
        assert "<<functions>>" not in text.split('class C2["View"]')[1]
        assert "A dashed arrow is an import" in text

    def test_no_import_note_when_there_are_no_imports(self):
        text = generate_component_detail(_snapshot([_class("A"), _class("B")], []), "module:lib")
        assert text is not None and "<sub>" not in text

    def test_one_function_too_many_is_listed_not_counted(self):
        node = _functions("a.js", [f"fn{i}" for i in range(MAX_MEMBERS + 1)])
        assert member_names(node) == (node.members, "")
        node.members.append("last")
        assert member_names(node) == (node.members[:MAX_MEMBERS], f"{MAX_MEMBERS + 2} functions, {MAX_MEMBERS} listed")

    def test_d2_and_plantuml_show_the_same(self):
        snap = self._small()
        d2 = D2Generator(ArchiLensConfig()).component_detail(snap, "module:lib")
        assert d2 is not None
        assert 'c0_utils_js: "utils.js\\nlib/utils.js\\netag()\\nwetag()"' in d2
        assert '"application.js\\nlib/application.js\\n12 functions, 8 listed\\nfn0()' in d2
        assert 'module.c1_application_js -> module.c0_utils_js: "imports"' in d2
        puml = PlantUMLGenerator(ArchiLensConfig()).component_detail(snap, "module:lib")
        assert puml is not None
        assert 'class "utils.js" as functions_lib_utils_js <<functions>> {\n  +etag()\n  +wetag()\n}' in puml
        assert "<<functions>> {\n  12 functions, 8 listed\n  +fn0()\n" in puml
        assert "functions_lib_application_js ..> functions_lib_utils_js : imports" in puml
        assert 'class "View" as class_lib_View_py_View\n' in puml  # a class is drawn as before

    def test_names_the_renderers_would_misread_are_escaped(self):
        # Found by rendering with the official tools: Mermaid reads a line with brackets as a
        # method (SvelteKit and Next.js route folders have them), and D2 reads "$" as a substitution.
        page = ArchNode(
            id="class:src/routes/(app)/[id]/page.ts:Page", name="Page", node_type=NodeType.CLASS,
            level=DiagramLevel.COMPONENT, file_path="src/routes/(app)/[id]/#1 {x}.ts", parent="module:lib",
        )
        snap = _snapshot([page, _functions("jq.js", ["$", "$format"])], [])
        mermaid = generate_component_detail(snap, "module:lib")
        assert mermaid is not None
        assert "        +src/routes/#40;app#41;/[id]/#35;1 #123;x#125;.ts\n" in mermaid
        assert "        +$()\n        +$format()\n" in mermaid
        d2 = D2Generator(ArchiLensConfig()).component_detail(snap, "module:lib")
        assert d2 is not None and "\\$()\\n\\$format()" in d2

    def test_a_function_only_module_gets_an_l2_file(self, tmp_path):
        snap = _analyze(_repo(tmp_path / "repo", EXPRESS))
        written = get_generator(ArchiLensConfig()).generate_all(snap, tmp_path / "out")
        assert "L2_lib_components.md" in {p.name for p in written}
        assert "<<functions>>" in (tmp_path / "out" / "L2_lib_components.md").read_text(encoding="utf-8")


class TestLargeModules:
    def _large(self) -> ArchSnapshot:
        nodes = [_class(f"K{i}") for i in range(10)]
        edges = [
            ArchEdge(source=nodes[7].id, target=nodes[2].id, edge_type=EdgeType.INHERITANCE),
            _imports_edge(nodes[2], nodes[7]),
            _imports_edge(nodes[9], nodes[7]),
            _imports_edge(nodes[0], nodes[1]),
        ]
        return _snapshot(nodes, edges)

    def test_everything_is_drawn_up_to_the_limit(self):
        snap = self._large()
        shown, edges, everything = select_l2(snap, "module:lib", limit=10)
        assert len(shown) == len(everything) == 10 and len(edges) == 4
        assert select_l2(snap, "module:lib", limit=0)[0] == everything  # 0 = no limit

    def test_beyond_it_the_most_connected_are_kept_in_their_original_order(self):
        shown, edges, everything = select_l2(self._large(), "module:lib", limit=3)
        # K7 has 3 relationships and K2 has 2; K0, K1 and K9 have 1 each, and a tie goes to the earlier one
        assert [n.name for n in shown] == ["K0", "K2", "K7"]
        assert len(everything) == 10
        assert {(e.source.rsplit(":", 1)[-1], e.target.rsplit(":", 1)[-1]) for e in edges} == {
            ("K7", "K2"), ("K2", "K7"),  # only those with both ends drawn
        }

    def test_the_note_and_the_table_say_what_is_missing(self):
        assert l2_note(10, 10) == ""
        text = generate_component_detail(self._large(), "module:lib", max_components=3)
        assert text is not None
        assert text.count("    class C") == 3
        assert "Showing 3 of 10 components: the ones with the most relationships inside the module." in text
        assert "Set diagrams.l2_max_components: 0 to draw all." in text
        assert "<details><summary>All 10 components</summary>" in text
        assert "| K0 | class | lib/K0.py |" in text and "| K9 | class | lib/K9.py |" in text

    def test_d2_and_plantuml_respect_the_limit(self):
        config = ArchiLensConfig()
        config.diagrams.l2_max_components = 3
        d2 = D2Generator(config).component_detail(self._large(), "module:lib")
        puml = PlantUMLGenerator(config).component_detail(self._large(), "module:lib")
        assert d2 is not None and puml is not None
        assert d2.count("lib/K") == 3 and "# Showing 3 of 10 components" in d2
        assert puml.count("\nclass ") == 3 and "' Showing 3 of 10 components" in puml

    def test_the_limit_is_configurable(self, tmp_path):
        assert ArchiLensConfig().diagrams.l2_max_components == 60
        (tmp_path / ".archilens.yml").write_text("diagrams:\n  l2_max_components: 0\n", encoding="utf-8")
        assert load_config(tmp_path).diagrams.l2_max_components == 0


# ---------------------------------------------------------------------------
# Pull requests, history, MCP
# ---------------------------------------------------------------------------


class TestDiff:
    def test_a_new_file_of_functions_is_reported_and_a_removed_one(self, tmp_path):
        base = _analyze(_repo(tmp_path / "base", EXPRESS))
        head = _analyze(_repo(tmp_path / "head", {**EXPRESS, "lib/view.js": "function View() {}\n"}))
        added = [e.description for e in compute_diff(base, head).entries]
        assert added == ["New file of functions lib/view.js added"]
        removed = compute_diff(head, base).entries
        assert [(e.description, e.severity) for e in removed] == [("File of functions lib/view.js removed", "info")]

    def test_an_import_inside_a_module_is_not_an_architecture_change(self, tmp_path):
        base = _analyze(_repo(tmp_path / "base", EXPRESS))
        files = {**EXPRESS, "lib/request.js": "var u = require('./utils');\n" + EXPRESS["lib/request.js"]}
        head = _analyze(_repo(tmp_path / "head", files))
        assert _imports(head) - _imports(base) == {("functions:lib/request.js", "functions:lib/utils.js")}
        assert compute_diff(base, head).entries == []

    def test_a_dependency_between_modules_still_is(self, tmp_path):
        base = _analyze(_repo(tmp_path / "base", EXPRESS))
        files = {**EXPRESS, "lib/utils.js": "var root = require('../index');\n" + EXPRESS["lib/utils.js"]}
        head = _analyze(_repo(tmp_path / "head", files))
        assert [e.description for e in compute_diff(base, head).entries] == ["New dependency: lib -> <root>"]

    def test_a_renamed_file_of_functions_is_not_a_moved_class(self, tmp_path):
        base = _analyze(_repo(tmp_path / "base", {"a/util.py": "def f():\n    pass\n", "b/x.py": "def g():\n    pass\n"}))
        head = _analyze(_repo(tmp_path / "head", {"b/util.py": "def f():\n    pass\n", "b/x.py": "def g():\n    pass\n"}))
        descriptions = [e.description for e in compute_diff(base, head).entries]
        assert not any(d.startswith("Moved") for d in descriptions)
        assert "New file of functions b/util.py added" in descriptions
        assert "File of functions a/util.py removed" in descriptions


def test_history_counts_only_dependencies_between_modules(tmp_path):
    repo = _repo(tmp_path, EXPRESS)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@e.com", "-C", str(repo)]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "v1"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    (head,) = analyze_evolution(repo, ArchiLensConfig()).refs
    assert head.dependencies == [("<root>", "lib")] and head.edge_count == 1  # not the 3 imports inside lib
    assert head.node_count == 2 + 4  # 2 modules + 4 files of functions


def test_mcp_lists_the_functions_and_counts_module_edges_only(tmp_path, monkeypatch):
    pytest.importorskip("mcp.server.fastmcp", reason="requires the 'mcp' extra (mcp 1.x)")
    from archilens.mcp import cache, server

    repo = _repo(tmp_path / "repo", EXPRESS)
    monkeypatch.setattr(cache, "_CACHE_DB", tmp_path / "cache" / "snapshots.db")
    for attribute in ("_snapshot", "_config", "_repo_path"):
        monkeypatch.setattr(server, attribute, None)
    server.init_server(repo)
    mcp = server.create_mcp_server()

    def ask(scope: str) -> str:
        result = asyncio.run(mcp.call_tool("get_architecture_context", {"scope": scope}))
        return (result[0] if isinstance(result, tuple) else result)[0].text

    l2 = ask("L2:lib")
    assert "Components : 4" in l2
    assert "  - lib/application.js (functions): init, use, listen" in l2
    l1 = ask("L1")
    assert "Summary: 2 modules, 1 edges" in l1
    assert "functions:" not in l1  # imports inside a module are not "key dependencies"

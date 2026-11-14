"""
Project types that used to come out empty: modern Node (.mjs/.cjs/.mts/.cts), Vue and
Svelte single-file components, and languages ArchiLens finds but cannot analyse yet,
which now say so instead of drawing modules with nothing between them.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from click.testing import CliRunner

from archilens.analyzers.dependencies import ANALYZED_LANGUAGES, unanalyzed_languages
from archilens.analyzers.discovery import EXTENSION_MAP, LANGUAGE_NAMES, discover_files
from archilens.analyzers.embedded import script_source
from archilens.cli import main
from archilens.config import ArchiLensConfig
from archilens.engine import analyze_repository
from archilens.generators.d2 import D2Generator
from archilens.generators.mermaid import generate_module_architecture
from archilens.generators.plantuml import PlantUMLGenerator
from archilens.models import ArchSnapshot, NodeType

ROOT = Path(__file__).parent.parent


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def _analyze(repo: Path) -> ArchSnapshot:
    return analyze_repository(repo, ArchiLensConfig(), use_ai=False)


def _module_edges(snap: ArchSnapshot) -> set[tuple[str, str]]:
    return {
        (e.source.removeprefix("module:"), e.target.removeprefix("module:"))
        for e in snap.edges
        if e.source.startswith("module:") and e.target.startswith("module:")
    }


class TestModernNode:
    def test_esm_and_commonjs_extensions_are_source_files(self, tmp_path):
        repo = _repo(tmp_path, {
            "index.mjs": 'import { helper } from "./lib/helper.mjs";\nexport const main = () => helper();\n',
            "lib/helper.mjs": "export function helper() { return 1; }\n",
            "tools/build.cjs": 'const { helper } = require("../lib/legacy.cjs");\n',
            "lib/legacy.cjs": "module.exports = { helper() {} };\n",
            "types/api.mts": 'import type { Shape } from "../lib/shape.cjs";\nexport type Api = Shape;\n',
            "lib/shape.cts": "export interface Shape { id: string }\n",
        })
        files, _ = discover_files(repo, ArchiLensConfig())
        assert {f.relative_path: f.language for f in files} == {
            "index.mjs": "javascript", "lib/helper.mjs": "javascript", "tools/build.cjs": "javascript",
            "lib/legacy.cjs": "javascript", "types/api.mts": "typescript", "lib/shape.cts": "typescript",
        }
        # "../lib/shape.cjs" is how TypeScript writes an import of shape.cts
        assert _module_edges(_analyze(repo)) == {("<root>", "lib"), ("tools", "lib"), ("types", "lib")}


class TestSingleFileComponents:
    VUE_APP = '<script setup lang="ts">\nimport Cart from "./components/Cart.vue";\nimport { total } from "./lib/money";\n</script>\n\n<template><Cart :total="total" /></template>\n'
    VUE_CART = "<template><p>cart</p></template>\n\n<script>\nexport default { name: 'Cart' }\n</script>\n"

    def test_script_source_keeps_line_numbers_and_detects_typescript(self):
        code, language = script_source("<template>\n  <p/>\n</template>\n<script lang='ts'>\nconst a: number = 1\n</script>\n", "vue")
        assert language == "typescript"
        assert code.splitlines()[4] == "const a: number = 1"  # line 5 in the file, line 5 in the code
        assert "<template>" not in code and "<p/>" not in code

    def test_two_script_blocks_and_plain_javascript(self):
        source = '<script context="module">\nexport const prerender = true;\n</script>\n<script>\nimport Nav from "./Nav.svelte";\n</script>\n<h1>hi</h1>\n'
        code, language = script_source(source, "svelte")
        assert language == "javascript"
        assert "prerender" in code and 'import Nav from "./Nav.svelte"' in code and "<h1>" not in code

    def test_other_languages_pass_through_untouched(self):
        assert script_source("<script>not html</script>", "python") == ("<script>not html</script>", "python")

    def test_vue_project_has_dependencies_and_components(self, tmp_path):
        repo = _repo(tmp_path, {
            "src/App.vue": self.VUE_APP,
            "src/components/Cart.vue": self.VUE_CART,
            "src/lib/money.ts": "export const total = 1;\n",
        })
        snap = _analyze(repo)
        assert _module_edges(snap) == {("src", "src/components"), ("src", "src/lib")}
        components = {n.name: n for n in snap.nodes if n.node_type == NodeType.COMPONENT}
        assert set(components) == {"App", "Cart"}
        assert components["Cart"].parent == "module:src/components"
        assert snap.unanalyzed == {}

    def test_svelte_project_resolves_component_imports(self, tmp_path):
        repo = _repo(tmp_path, {
            "src/routes/Home.svelte": '<script lang="ts">\n  import Button from "../ui/Button.svelte";\n  import { api } from "../lib/api";\n</script>\n<Button/>\n',
            "src/ui/Button.svelte": "<button><slot/></button>\n",  # no script block at all
            "src/lib/api.ts": "export const api = {};\n",
        })
        snap = _analyze(repo)
        assert _module_edges(snap) == {("src/routes", "src/ui"), ("src/routes", "src/lib")}
        assert {n.name for n in snap.nodes if n.node_type == NodeType.COMPONENT} == {"Home", "Button"}

    def test_extensionless_import_prefers_code_over_a_component(self, tmp_path):
        repo = _repo(tmp_path, {
            "src/app/main.ts": 'import { cart } from "../shop/Cart";\n',
            "src/shop/Cart.ts": "export const cart = 1;\n",
            "src/widgets/Cart.vue": "<template/>\n",
        })
        assert _module_edges(_analyze(repo)) == {("src/app", "src/shop")}


class TestLanguagesNotAnalysedYet:
    def test_every_recognised_language_has_a_display_name(self):
        assert set(EXTENSION_MAP.values()) <= set(LANGUAGE_NAMES)
        assert {"python", "javascript", "typescript", "java", "go", "vue", "svelte"} == set(ANALYZED_LANGUAGES)

    def test_counts_only_non_test_files_in_unsupported_languages(self, tmp_path):
        repo = _repo(tmp_path, {
            "api/app.py": "x = 1\n",
            "Shop/Orders/OrderService.cs": "class OrderService {}\n",
            "Shop/Billing/Invoice.cs": "class Invoice {}\n",
            "Shop/Orders/tests/OrderTests.cs": "class OrderTests {}\n",
            "core/lib.rs": "pub fn f() {}\n",
        })
        files, _ = discover_files(repo, ArchiLensConfig())
        assert unanalyzed_languages(files) == {"C#": 2, "Rust": 1}

    def test_the_note_names_languages_most_files_first(self):
        note = ArchSnapshot(project_name="p", unanalyzed={"Rust": 1, "C#": 1200, "Kotlin": 8}).unanalyzed_note()
        assert note.startswith("Not analysed: 1,200 C# files, 8 Kotlin files and 1 Rust file. ")
        assert ArchSnapshot(project_name="p").unanalyzed_note() == ""

    def test_analysis_warns_once_and_every_format_says_so(self, tmp_path, caplog, monkeypatch):
        repo = _repo(tmp_path, {"api/app.py": "x = 1\n", "Shop/Orders/OrderService.cs": "class A {}\n"})
        monkeypatch.setattr("archilens.engine._UNANALYZED_WARNED", set())  # process-wide; start clean
        with caplog.at_level(logging.WARNING, logger="archilens.engine"):
            snap = _analyze(repo)
            _analyze(repo)  # diff and history analyse the same repository more than once
        warnings = [r.getMessage() for r in caplog.records if "Not analysed" in r.getMessage()]
        assert warnings == [
            "Not analysed: 1 C# file. ArchiLens groups them into modules but does not read their imports or "
            "classes yet, so those modules show no dependencies."
        ]
        assert "> **Not analysed: 1 C# file." in generate_module_architecture(snap)
        assert "# Not analysed: 1 C# file." in D2Generator(ArchiLensConfig()).module_architecture(snap)
        assert "' Not analysed: 1 C# file." in PlantUMLGenerator(ArchiLensConfig()).module_architecture(snap)

    def test_fully_supported_project_gets_no_note(self, tmp_path):
        snap = _analyze(_repo(tmp_path, {"a/x.py": "import b.y\n", "b/y.py": "z = 1\n"}))
        assert snap.unanalyzed == {}
        assert "Not analysed" not in generate_module_architecture(snap)

    def test_init_says_so_up_front(self, tmp_path):
        _repo(tmp_path, {"Shop/Orders/OrderService.cs": "class A {}\n", "Shop/Billing/Invoice.cs": "class B {}\n"})
        out = " ".join(CliRunner().invoke(main, ["init", "--repo", str(tmp_path)]).output.split())
        assert "Found 2 C# files in 2 modules." in out
        assert "Not analysed: 2 C# files." in out


def test_ci_runs_on_windows_and_macos_too():
    matrix = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["test"]["strategy"]["matrix"]
    assert {"windows-latest", "macos-latest"} <= {entry["os"] for entry in matrix["include"]}
    assert matrix["os"] == ["ubuntu-latest"] and len(matrix["python-version"]) == 5


class TestFrameworkAliases:
    """Import aliases a framework defines by convention, with no config file declaring them."""

    def test_sveltekit_lib_alias_and_virtual_modules(self, tmp_path):
        repo = _repo(tmp_path, {
            "package.json": "{}",
            "svelte.config.js": "export default {};\n",
            "src/routes/+page.svelte": (
                '<script>\n  import { api } from "$lib/api";\n  import Nav from "$lib/Nav.svelte";\n'
                '  import { page } from "$app/stores";\n  import { env } from "$env/dynamic/public";\n</script>\n'
            ),
            "src/lib/api.js": "export const api = {};\n",
            "src/lib/Nav.svelte": "<nav/>\n",
            "src/app/stores.js": "export const decoy = 1;\n",  # $app is SvelteKit's, not this folder
        })
        assert _module_edges(_analyze(repo)) == {("src/routes", "src/lib")}

    def test_lib_alias_is_relative_to_the_app_in_a_monorepo(self, tmp_path):
        repo = _repo(tmp_path, {
            "package.json": "{}",
            "apps/web/package.json": '{"name": "web"}',
            "apps/web/svelte.config.js": "export default {};\n",
            "apps/web/src/routes/+page.svelte": '<script>import { a } from "$lib/a";</script>\n',
            "apps/web/src/lib/a.js": "export const a = 1;\n",
            "apps/web/src/hooks.js": 'import { a } from "$lib/a";\n',
            "src/lib/a.js": "export const wrong = 1;\n",  # the repository root's src/lib is a different app
        })
        snap = _analyze(repo)
        # apps/web is one workspace module, so its own $lib imports stay inside it and
        # nothing points at the root's src/lib.
        assert _module_edges(snap) == set()

    def test_at_and_tilde_mean_src_or_the_app_root(self, tmp_path):
        repo = _repo(tmp_path, {
            "package.json": "{}",
            "src/views/Home.vue": '<script setup>\nimport { useCart } from "@/stores/cart";\nimport cfg from "~/config/site";\n</script>\n',
            "src/stores/cart.js": "export const useCart = () => 1;\n",
            "config/site.js": "export default {};\n",  # Nuxt-style: ~ is the app root when src/ has no match
        })
        assert _module_edges(_analyze(repo)) == {("src/views", "src/stores"), ("src/views", "config")}

    def test_the_projects_own_tsconfig_paths_win(self, tmp_path):
        repo = _repo(tmp_path, {
            "package.json": "{}",
            "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./app/*"]}}}',
            "web/page.ts": 'import { x } from "@/core/x";\n',
            "app/core/x.ts": "export const x = 1;\n",
            "src/core/x.ts": "export const x = 2;\n",
        })
        assert _module_edges(_analyze(repo)) == {("web", "app/core")}

    def test_scoped_npm_packages_are_still_external(self, tmp_path):
        repo = _repo(tmp_path, {
            "package.json": "{}",
            "src/a/x.ts": 'import { ref } from "@vue/reactivity";\nimport kit from "@sveltejs/kit";\n',
            "src/vue/reactivity.ts": "export const decoy = 1;\n",
        })
        assert _module_edges(_analyze(repo)) == set()

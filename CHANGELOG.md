# Changelog

All notable changes to ArchiLens are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [0.16.0] — 2026-11-14

More kinds of projects work. Each gap below was confirmed on a probe project or a real repository
(VitePress, the SvelteKit RealWorld app) before it was fixed.

### Added
- **Modern Node projects.** `.mjs`, `.cjs`, `.mts` and `.cts` files are source files. They were not
  recognised at all, so an ESM project came out as "No source files found".
- **Vue and Svelte.** Single-file components are read through their `<script>` blocks (TypeScript when
  a block says `lang="ts"`), with line numbers preserved. Each file is also a component on the L2
  diagram. Both project types used to find 0 files.
- **Framework import aliases** that no config file declares: `$lib` (SvelteKit; `$app` and `$env` stay
  external) and `@/`, `~/` (Vue CLI, Nuxt, Vite), relative to the nearest `package.json` or Svelte
  config. A project's own `tsconfig` paths are tried first. The SvelteKit RealWorld app went from 0
  dependencies to `src/routes → src/lib`.
- **A plain statement when a language isn't analysed.** C#, Kotlin, Rust, Ruby, PHP, C/C++, Swift and
  Scala files are grouped into modules, but their imports and classes are not read yet. That used to
  produce modules with nothing between them and no explanation. Now `init`, the `analyze` log (once per
  run) and a note under the module diagram say, for example, "Not analysed: 120 C# files".
- **Example configs for real projects** in `docs/configs/`: Spring, written against spring-petclinic
  (by hiteshchouhan22, #35), and Django, written against django/django (by Taranemp, #36). The first
  outside contributions to ArchiLens. Thank you both.

### Changed
- CI also runs the tests on Windows and macOS. Two Windows-only bugs had been found by hand.
- `init` names languages properly ("Found 4 Go files", not "4 go files").

---

## [0.15.1] — 2026-10-25

### Fixed
- **Diagram colors are readable in light and dark themes.** Measured with the WCAG contrast formula:
  - External systems were white text on `#999`, at 2.8:1; they are now slate `#64748b`, at 4.8:1.
  - Databases and ungrouped modules were 3.5:1; now 4.8:1.
  - The PR change diagram's "new" green was 3.4:1; the new, removed and resized colors now match
    GitHub's own palette, at 4.9:1 or better.
  - Every box now has an outline lighter than its fill, so boxes stay visible in GitHub's dark mode.
    The actor box used to be 1.9:1 against a dark page.
  - The same applies to Mermaid, D2 and PlantUML, and to the interactive viewer's capability colors,
    two of which were below 4.5:1 with their light text.
- Website: every diagram label was gray instead of white. The page's own `.node` class, used for the
  pipeline steps, also matched Mermaid's diagram nodes, so its muted text color applied to all
  labels. Renamed. An axe-core contrast audit of the page now reports no violations.

---

## [0.15.0] — 2026-10-25

### Changed
- **The module diagram (L1) stays readable on large projects.** Found on Django 6.1: 17 modules and
  109 dependencies, each with an "N imports" label, so Mermaid shrank the modules to tiny boxes under a
  wall of text.
  - Line width now shows how many imports a dependency carries: 1px for one, up to 4px for the
    heaviest in the diagram, on a log scale so the differences stay visible whether the heaviest is 5
    imports or 500. The labels are gone. All three formats: Mermaid `linkStyle`, D2
    `style.stroke-width`, PlantUML `thickness`.
  - Above `diagrams.l1_max_edges` (default 40; 0 draws all), L1 draws the heaviest dependencies, after
    first giving every module its strongest one so nothing looks unconnected, and says how many it
    left out.
  - Nothing is lost: the Mermaid L1 ends with a collapsible table of every dependency and its import
    count.

---

## [0.14.0] — 2026-10-25

Found by running ArchiLens the way a new user would on six more projects: Flask, FastAPI, Django,
Express, NestJS and Spring PetClinic.

### Changed
- **Example apps, docs and benchmarks no longer crowd the module diagram.** Root-level `examples/`,
  `example/`, `samples/`, `sample/`, `demo/`, `demos/`, `docs/`, `docs_src/`, `benchmarks/` and
  `benchmark/` are excluded by default. Express's L1 showed 26 example apps beside its one library;
  FastAPI's had 100 modules, about 80 of them documentation snippets. Only the root counts, so Java
  packages such as `com/example/demo` (Spring Initializr's default) and PetClinic's
  `org/springframework/samples` are unaffected.
- **`archilens init` also leaves out root `integration/`, `scripts/`, `tools/`, `fixtures/` and
  `playground/` folders**, visibly: each gets its own line in the written `exclude` list with the
  reason, and `init` says what it left out. On NestJS this drops 28 integration-test apps from the
  diagram. The written list now starts from the full default list; it had drifted and was missing
  `.venv`, `vendor`, `target`, `coverage` and others.
  Module counts after `init`: Express 27 → 2, FastAPI 100 → 7, NestJS 83 → 9, Flask 8 → 3.

### Fixed
- **`analyze` crashed on Windows when its output was redirected** (or on a Windows CI runner) for any
  repository large enough to show the progress spinner: the legacy cp1252 code page can't encode it,
  and the run died with `UnicodeEncodeError`. Characters the console can't show now print as `?`.

---

## [0.13.0] — 2026-10-25

Found by running ArchiLens on an outside project the way a new user would: gin-gonic/gin, Go.

### Fixed
- **`archilens init` broke analysis of Go, Rust, C# and other projects.** The config it wrote had an
  active `languages: ["python", "typescript", "java"]`, so on gin `analyze` found 0 files and
  `check` passed against nothing. It also wrote a fictional order-management example as live
  config: PostgreSQL, Redis, Stripe and SendGrid appeared on every new user's L0 diagram.
  `init` now runs a quick static discovery and writes a config for the repository:
  - its name, the languages found, and its modules by size;
  - only settings true for any repository (two generic rules) are active;
  - capabilities, external systems, a module rule and a capability rule are commented examples built
    from the real module paths.
  On gin it now finds all 99 Go files in 8 modules.
- **A config section whose contents are all commented out no longer crashes.** Such a section is YAML
  null; `evolution:`, `analysis:`, `ci:` and the others now fall back to their defaults.
- Module names keep their capitals: `ginS` is shown as "GinS", not "Gins", and `REST-API` stays
  "REST API".
- Dependency changes in the report read `orders -> payments` instead of
  `module:orders -> module:payments (dependency)`.

### Added
- **The AI answer cache deletes answers unused for 90 days**, once per run, so the file doesn't grow
  forever on a developer machine or in the action's CI cache. Older cache files are migrated in place.
- Screenshots of a real PR comment in the README and on the website (`docs/images/`). The new
  `scripts/pr_comment_screenshot.py` takes them from any public PR.

---

## [0.12.0] — 2026-10-25

### Added
- **AI usage is reported.** The AI client totals requests, cache hits and tokens. Input includes
  prompt-cache reads and writes, and output includes thinking. `analyze` logs the totals, and the
  `diff --ai` report and PR comment credit them under the summary, e.g. *AI usage: 1 request: 1,039
  input and 275 output tokens*. Tokens, not dollars, because prices change.
- **The GitHub Action keeps the AI answer cache between runs.** With `enable-ai`, it restores and
  saves `~/.archilens/ai_cache.db` with `actions/cache`. A re-run, or a push that doesn't change
  what a request contains, is answered from the cache at no cost. That matters most with
  `ai-diagrams`, whose module summaries rarely change.

### Changed
- CI lints `tests/` and `scripts/` as well as `archilens/`. The older test files' unused imports,
  whitespace and a mutable default argument are fixed.

---

## [0.11.1] — 2026-10-25

### Changed
- **The diff report moved from `archilens.analyzers.diff` to `archilens.generators.diff_report`.**
  Analysis now only produces data, and turning it into Markdown and Mermaid is the generators' job.
  The AI review of PR #22 pointed out that the analyzers depended on the generators, and that PR #22
  had made that dependency stronger. A new rule in ArchiLens's own `.archilens.yml`,
  `analysis-without-rendering`, fails the build if it comes back.
  **If you import it in Python:** `from archilens.generators.diff_report import format_diff_as_markdown`.
  The report itself, the CLI and the GitHub Action are unchanged.

---

## [0.11.0] — 2026-10-25

### Added
- **AI review in the PR comment.** `archilens diff --ai`, and the GitHub Action with
  `enable-ai: "true"`, open the report with:
  - a few sentences on what the change does to the architecture;
  - up to four points worth a reviewer's attention;
  - a one-line description of each new module, in the change table;
  - the process flow of each entry-point file the change adds, as a collapsed sequence diagram.
  It costs one request, plus one per new entry point (at most two). The tables, rule results and
  `fail-on-violations` are unchanged and never use AI. Without credentials, or if the request fails,
  the report is the static diff with a warning in the log. Model-written text is sanitized: no HTML or
  images, no @-mentions, and it can't change the action's `violations-count`.
- Action input `ai-diagrams` (default `"false"`): with `enable-ai`, also adds AI to the uploaded
  diagrams. That costs about one request per module per run, so it is now a separate switch.

### Changed
- **`enable-ai: "true"` alone no longer adds AI to the uploaded diagrams**; add
  `ai-diagrams: "true"` for that. 0.10.0 workflows that set `enable-ai: "true"` now get the AI
  review in the comment instead.
- ArchiLens's own PR workflow runs the AI review.

---

## [0.10.0] — 2026-10-25

### Changed
- **The GitHub Action's `enable-ai` input now defaults to `"false"`.** AI only adds module summaries,
  process flows and patterns to the uploaded diagrams artifact. The PR comment and the rule checks never
  used it. With the old default, workflows without a key installed the Anthropic SDK and logged a
  "no credentials" warning on every run. AI is now an explicit opt-in: only the exact value `"true"`
  turns it on.
  **Upgrading:** if you pass `anthropic-api-key` and want the AI diagrams, add `enable-ai: "true"`.

---

## [0.9.1] — 2026-10-25

Fixes found by the first run of the AI features against the live Anthropic API.

### Fixed
- **Process flows now work without configuration.** Flows were only inferred for files listed under
  `analysis.entry_points`. That list is empty by default, so flows were silently skipped. When it is
  unset, ArchiLens now detects up to 5 entry points from `pyproject.toml` console scripts,
  `package.json` `bin` / `main`, and conventional file names (`main.go`, `app.py`,
  `*Application.java`, ...), and logs which files it picked. With none found, it says how to
  configure them. On ArchiLens itself it picks the CLI, the MCP server and the viewer.
- `archilens init` no longer writes four example entry-point patterns that matched nothing in most
  repositories and switched the detection off. They are now commented out.
- AI summaries in the L1 diagram (Mermaid labels, D2 tooltips) are cut at a word boundary and end in
  "…", instead of mid-word.
- The flow prompt asks the model to use one name per participant, so a sequence diagram no longer shows
  the same caller twice ("AI Coding Agent" and "Agent"). It also mentions CLI and `main` entry points,
  not just HTTP handlers.
- `INDEX.md` now describes every diagram: the module summary for L2, and the flow description for L3.
  The Description column was always empty, and the Mermaid index was a duplicate of the shared one.

---

## [0.9.0] — 2026-10-25

### Added
- **The GitHub Action is ready for the Marketplace.** It now lives at the repository root, so workflows
  use `saurabh-oss/archilens@v0.9.0`, and it has the `author` and `branding` (icon and color) the
  Marketplace listing needs. `saurabh-oss/archilens/github_action@<version>` keeps working: that path
  holds an identical copy, and a test keeps the two files in sync. ArchiLens's own PR workflow now runs
  the root action, the same one Marketplace users get.

---

## [0.8.0] — 2026-10-25

### Changed
- **The AI libraries are now optional extras.** `pip install archilens` installs the static analysis,
  diagrams, diff, rules and history without any LLM SDK: **31 MB and 22 packages, down from 243 MB and
  80** (LiteLLM alone was 122 MB). For AI features, install `archilens[ai]` (Claude) or
  `archilens[litellm]` (other providers).
  **Upgrading with AI in use:** `pip install "archilens[ai]"`. Without it, `analyze` prints one warning
  naming the missing extra and runs the static analysis. The GitHub Action installs the SDK itself when
  `enable-ai` is on.
- Removed the unused `jinja2` dependency.

### Fixed
- `archilens serve-mcp` without the `mcp` extra, and `archilens serve` without the `viewer` extra,
  crashed with a traceback (the checked import wasn't the one that failed). Both now print which
  extra to install and exit 1.

---

## [0.7.0] — 2026-10-25

### Changed
- **`archilens history` runs the real analysis on every ref.** Each ref's source files are read from the
  git object database into a temporary directory and analysed like `archilens analyze` (module layout,
  import resolution). Previously it counted files as "nodes" and lines starting with `import` as
  "edges". The table now shows modules, LOC, classes and module dependencies.
- **Dependency changes over time**: the report lists which module dependencies appeared or
  disappeared at each ref, and `history` prints how many.
- **Default refs work.** Without `--refs`, `history` expands `evolution.baseline_refs` (globs like
  `tags/v*` match tags in version order), skips refs that don't exist, falls back to all semver tags,
  and appends HEAD when it is past the last ref. Previously the default (`main` and a literal
  `tags/v*`) failed on repositories without a `main` branch, and on repositories with one it analysed
  only that branch. `evolution.baseline_refs` now defaults to empty, meaning semver tags plus HEAD.

---

## [0.6.2] — 2026-10-25

### Fixed
Every diagram is now checked with the official renderers (mermaid-cli, D2, PlantUML), including a
fixture whose names contain quotes, braces, `#`, `;`, `<>` and non-ASCII characters. That found:
- **D2 module and component diagrams drew every connected shape twice.** Connections named shapes by
  bare key while the shapes sat inside capability or module containers, so D2 created detached
  duplicates. Connections now use the full path (`container.shape`).
- **D2 sequence diagrams didn't compile** when a step had data (`.->` and unquoted labels). Steps now
  use `->` with quoted labels; async steps are dashed and guarded steps are wrapped in a labelled
  group, as in the Mermaid and PlantUML output.
- **D2 failed on summaries containing `<...>`** (tooltips are parsed as Markdown); angle brackets
  become `‹ ›`. D2 class boxes are now plain rectangles (interfaces dashed) instead of code-styled
  `shape: class` boxes.
- **IDs with punctuation broke all three formats** (for example a project named `Shop (demo)`): IDs
  are now ASCII letters, digits and underscores only; sequence-diagram participants and Mermaid
  classes use position-based IDs, so distinct names can't collide.
- **Mermaid sequence diagrams**: `;` and `#` in step text ended the statement or started an entity
  and are now escaped; calls no longer open activation bars that were never closed, and async steps
  use Mermaid's open arrow (`-)`).
- **PlantUML**: multi-line descriptions broke out of comments, and quotes broke labels.
- **Output file names** could contain characters invalid on Windows (`<>:"`) or in Markdown links
  (`#`, `;`), and two modules with the same name overwrote each other's L2 file. Names are now
  portable slugs, made unique when they collide.

### Added
- `scripts/render_check.py` and a **"Diagrams render" CI job** that renders every level in every
  format with the official tools on each push and pull request.

---

## [0.6.1] — 2026-10-25

### Fixed
- **AI outlines could include one-line bodies.** A declaration with its body on the same line
  (`def f(): return x`, `class A: x = 1`, `func F() { return x }`) was sent to the model whole, despite
  the documented "signatures only". Outline lines are now cut at the end of the signature: for Python
  at the `:` that closes the header, for brace languages at the first `{`.

---

## [0.6.0] — 2026-10-25

### Added
- **Module summaries see the code's shape.** The prompt now includes a signature outline of the module's
  largest files (class, function and type declarations, no bodies, capped at about 6,000 characters),
  built for Python, JavaScript/TypeScript, Java and Go. Previously the model saw only the module name
  and class names.
- **AI answers are cached by content** (`~/.archilens/ai_cache.db`, `ai.cache: true` by default). Any
  change to the model, effort, schema or prompt (including the code in it) misses the cache; unchanged
  modules are not re-summarised, which cuts cost and keeps summaries stable between runs.
- **Capability suggestions are used**: for modules with no configured capability, the AI's suggestion is
  stored as `suggested_capability` and shown in `analyze` output and MCP `get_module_context`, and
  `find_capability_owner` matches against it. It is never assigned, because capabilities drive CI rules.

### Fixed
- Process-flow inference always told the model the entry point was an `http_handler`; it now passes
  the `type` configured for the matching `analysis.entry_points` pattern.

---

## [0.5.0] — 2026-10-25

### Added
- **Change diagram in the diff report and PR comment.** A Mermaid diagram shows only the modules and
  module dependencies that changed: new, removed and resized modules, and new, removed or re-weighted
  dependencies (coloured, with import counts). A new dependency that breaks a `forbidden-dependency`
  rule is drawn thick red and labelled with the rule. Unchanged modules appear only as endpoints;
  above 30 modules or 40 dependencies the diagram is replaced by a one-line summary.

### Changed
- **`archilens diff` recognises moved files.** A class that only moved to another file (same name,
  unambiguous on both sides) is no longer reported as one removal plus one addition; each file move is
  a single `moved` entry listing its classes. Replaying the diff of PR #7 (which turned three modules
  into packages) shrinks the report from 53 to 36 entries.

---

## [0.4.1] — 2026-10-25

### Added
- **ArchiLens checks its own pull requests.** The repository's `.archilens.yml` now describes ArchiLens
  itself (capabilities and rules), and `.github/workflows/archilens.yml` runs the composite action on
  every PR against the code under review.
- Composite action input **`install`** (default `archilens`): what to `pip install`, so workflows can
  pin a release (`archilens==0.4.1`) or test a local checkout (`.`).

### Changed
- **ArchiLens's own dependency cycle is gone.** `config`, `engine` and `rules` are now packages
  (`archilens/config/`, …) instead of modules beside `cli.py`, so the top-level module only holds the
  CLI and nothing depends on it. Import paths are unchanged (`from archilens.config import …`).
  The repository's `no-cycles` rule is now `fail`, with a new rule that nothing imports the CLI.
- The MCP server reads the ArchiLens version for its cache key from package metadata.

### Fixed
- `archilens init` wrote a minimal stub when installed from PyPI, because the annotated example config
  wasn't part of the package. The example now ships as `archilens/templates/archilens.example.yml`,
  and `init` fills in the repository's name.

---

## [0.4.0] — 2026-10-24

### Added
- **Rule types.** Rules now declare `type: forbidden-dependency | no-cycles | max-fan-out`; the rule
  `name` is just a label. Rules named `no-layer-skip` or `max-fan-out` without a `type` keep working.
- **Capability rules**: `forbidden-dependency` can select either side by business capability
  (`from_capability` / `to_capability`) as well as, or instead of, by path glob.
- **`no-cycles` rule type**, reported once per cycle of modules.
- **Baseline for pull requests.** `archilens diff` evaluates rules on both refs; violations already on
  the base ref are listed as "existing" (folded in the PR comment) and don't fail the build. New
  `ci.fail_on: new | all` (default `new`).
- **`archilens check`** evaluates the rules on the working tree and exits 1 on a `fail` rule
  (`--json-output` for tooling).
- MCP `check_dependency_allowed` uses the same rules: capability rules, imports that would close a
  cycle, and imports that would exceed a fan-out limit.

### Changed
- **A rule that can never fire is now a configuration error**: an unknown or missing `type`, a
  `forbidden-dependency` without both sides, a `max-fan-out` without a positive `threshold`, or an
  `action` other than `warn` / `fail`. Previously such rules were silently ignored.
- **`archilens diff` fails only on new violations by default.** Set `ci.fail_on: all` for the previous
  behaviour. The GitHub Action's `fail-on-violations` and `violations-count` follow the same rule.
- Rules consider only module dependency edges; `max-fan-out` previously also counted class inheritance
  edges.

---

## [0.3.0] — 2026-10-24

### Added
- **Per-language import resolution.** Imports are resolved to files in the repository, then
  aggregated into module edges:
  - Python: absolute imports against the repository root, `src/` and the file's own source root,
    plus relative imports (`.x`, `..x`). Example folders such as `docs_src/` no longer shadow
    third-party packages of the same name.
  - JavaScript/TypeScript: relative paths with extension and `index` probing (including `.js`
    specifiers for `.ts` sources), `tsconfig.json`/`jsconfig.json` `baseUrl` and `paths` (comments,
    trailing commas and relative `extends` supported), and workspace packages by `package.json` name.
    Re-exports (`export … from`), `require()` in TypeScript and side-effect imports are now extracted.
  - Java: fully qualified, wildcard and static imports, via each file's `package` declaration.
  - Go: import paths under any `go.mod` module in the repository.
- **Layout-aware module grouping.** Modules are named relative to source roots (`src/`,
  `src/main/java`, `go.mod` directories, workspace packages) and single-child "trunk" directories are
  skipped, instead of always using the first two directories. Each JS/TS workspace package is one
  module. New `analysis.module_depth` setting (default 2).
- `e2e/` directories are treated as tests.

### Changed
- **Module IDs change for some layouts**: `src/`-layout Python, Maven/Gradle Java, JS/TS monorepos,
  and repositories whose code all sits under one top-level directory. Capability and rule patterns
  written as `dir/**` keep matching sub-modules of `dir`; patterns that name an old module exactly may
  need updating. Flat repositories with several top-level directories keep their module IDs.
- An edge's weight counts each (file, imported module) pair once; repeated mentions of the same import
  in a file (e.g. Go import blocks) were counted more than once.
- `archilens history` groups files into modules the same way as `analyze` and leaves out test files,
  so its module counts match.
- The MCP server maps a file to its module by longest directory prefix, matching discovery.

On the 7-repository benchmark (flask, fastapi, express, nest, spring-petclinic, gin, django), the five
repositories that had **no** dependency edges in 0.2.2 now have them: flask 0 → 9, express 0 → 26,
nest 0 → 172, spring-petclinic 0 → 4, gin 0 → 11. In a manual sample of 40 resolved cross-module
imports, every target was the correct file, and 3 of about 3,300 relative imports were unresolved (all
pointing at generated files not in the repository).

---

## [0.2.2] — 2026-10-24

### Changed
- **Default AI model is now `claude-opus-5-5`**, replacing the deprecated `claude-sonnet-4-20250514`.
  A new `ai.effort` setting (default `"low"`) controls reasoning effort on Claude 5-generation models;
  `claude-haiku-5-5` is documented as a cheaper option for summaries.
- **Claude 5-generation models use structured outputs** instead of a forced tool call, which
  `claude-opus-5-5` and `claude-sonnet-5-5` reject. Older models keep the forced tool call. Requests to
  `claude-opus-5-5`, `claude-opus-5`, `claude-fable-5-1` and `claude-sonnet-5-5` enable server-side
  refusal fallback. Requires `anthropic>=2.3`.
- **AI is skipped with a single warning when no Anthropic credentials are available**, or the model
  doesn't exist, instead of logging two warnings per module. Credentials are checked once up front
  with the Models API, which costs no tokens.
- **Files ignored by `.gitignore` are no longer analysed.** Inside a Git work tree, files come from
  `git ls-files`. The default excludes now also cover `.venv`, `site-packages`, `.tox`, `target`,
  `vendor`, `.next`, `coverage` and `*.min.js`. `archilens init` no longer writes an `exclude` list
  that replaced these defaults.
- **Files under `test/`, `tests/`, `__tests__/`, `spec/` and `testdata/` directories are treated as
  tests** at any depth, so a top-level `tests/` package no longer appears as the largest module.
- **The viewer binds to `127.0.0.1` by default**; `--host` changes it. `--debug` (the Werkzeug
  debugger) is refused unless the host is loopback.
- **The MCP server re-analyses when the repository changes**: a new commit, uncommitted edits,
  a `.archilens.yml` change or an ArchiLens upgrade. Previously a snapshot was reused for as long as
  HEAD stayed the same, including across ArchiLens upgrades.
- The `serve-mcp` setup hint now shows `claude mcp add` instead of a non-existent config path.
- The composite action writes the report to the job summary, and no longer fails when it can't
  comment on a fork PR.

### Fixed
- **Analysis is much faster**: each file is parsed once instead of three times, and
  Tree-sitter queries are compiled once per language. On nestjs/nest (1,521 TypeScript files) the
  dependency stage went from 44 s to 0.5 s.
- Cycle detection no longer enumerates every cycle, which could take exponential time on dense graphs.
- **Output is reproducible**: `INDEX.md` no longer embeds a timestamp (so the diagram workflow no
  longer commits on every push), paths are POSIX-style on every OS, and edges and diff entries are
  sorted.
- `archilens diff` reported new classes as "New module added"; entries now name the node type.
  Removing a class is `info`; removing a module stays a `warning`.
- AI summaries containing quotes, `<`, `>`, `|` or newlines broke Mermaid and D2 syntax; labels are
  now escaped.
- The viewer inserted module names, AI summaries and error messages into the page as raw HTML;
  they are now escaped.

### Docs
- README comparison table corrected against each competitor's current docs, with the date checked,
  plus a note on current language limitations.
- New sections for the MCP server and the composite action; rule types documented.
- Landing page claims about AI and Tree-sitter corrected.

---

## [0.2.1] — 2026-09-30

### Fixed
- **Capability mapping and CI rules now match the documented glob style.** A pattern
  such as `src/orders/**` matches the module `src/orders` itself; previously it matched
  nothing, so capabilities were never assigned and `no-layer-skip` rules never fired.
  Matching is now case-sensitive and separator-normalised on every OS. The same fix
  applies to the MCP `check_dependency_allowed` tool.
- **`archilens serve-mcp` works again on a fresh install.** The `mcp` extra is pinned to
  `<2`, because mcp 2.x removed `mcp.server.fastmcp`.
- **`serve-mcp --transport sse` no longer crashes** with a `TypeError` on startup.
- **MCP `get_module_context`** returned the parent module for files in a nested module
  (e.g. `pkg` instead of `pkg/sub`); the most specific module now wins.
- **`archilens diff` prints only the markdown report on stdout**, unwrapped. Logs and
  status messages from every command now go to stderr, so `archilens diff > diff.md`
  and `archilens analyze --json-output > snapshot.json` produce clean files, and the
  stdio MCP transport is no longer at risk from log lines on stdout.
- **GitHub Action**: finds and updates its previous PR comment instead of posting a new
  one on every push; `fail-on-violations` now fails the job when a rule with action
  `fail` is violated; the `diagrams-path`, `has-changes` and `violations-count` outputs
  are set; a failed diff is reported as an error instead of being posted as a comment.
- `archilens.__version__` reported `0.1.0`; the version is now defined once, in
  `archilens/__init__.py`.

### Changed
- `serve-mcp` has a new `--host` option for the SSE transport, defaulting to
  `127.0.0.1` (the previous code intended `0.0.0.0`).
- Workflows and the composite action use Node 24 releases of the GitHub actions
  (`checkout@v7`, `setup-python@v7`, `github-script@v9`, `upload-artifact@v7`,
  `download-artifact@v8`, `codecov-action@v7`).
- CI lint job is green: Ruff lint and format pass, and mypy strict passes with no errors.
  CI installs the `viewer` and `mcp` extras so both are tested and type-checked.
- README, landing page and CONTRIBUTING document installation from PyPI.

---

## [0.2.0] — 2026-06-10

### Added
- **MCP server** (`archilens serve-mcp`): exposes architecture knowledge to AI coding agents
  (Claude Code, Cursor, Copilot) via four tools: `get_module_context`,
  `check_dependency_allowed`, `find_capability_owner`, `get_architecture_context`.
  Supports stdio and SSE transports. Snapshot cache backed by SQLite at
  `~/.archilens/snapshots.db` — avoids re-analysis on repeated agent sessions.
- **Cytoscape.js interactive graph** for L1 module architecture. Handles 500+ nodes
  with compound capability groups, LOC-proportional node sizing, weighted edges,
  click-to-drill to L2, hover tooltips, and selectable layout algorithms.
  Mermaid retained for L2 class diagrams and L3 sequence diagrams.
- New `/api/graph/l1` endpoint returning Cytoscape-format JSON with capability parent
  nodes, module nodes, and weighted dependency edges.

### Changed
- **AI module**: all three analysis functions (`infer_process_flows`,
  `generate_module_summaries`, `detect_patterns`) now use Anthropic tool use
  (`tool_choice={"type":"tool"}`) for deterministic structured output — no more
  JSON fence-stripping. Non-Anthropic providers retain the text-parsing fallback.
- `generate_module_summaries` now runs up to 5 completions in parallel via
  `ThreadPoolExecutor` — ~12x faster on large repos.
- `ArchSnapshot.version` renamed to `project_type` to correctly store
  `monolith|library|service` instead of a version number.

### Fixed
- Diff engine now emits `modified` entries for real signals: node LOC changes >20%
  between refs, and edge weight (coupling) changes >20%.
  Previously `0 modified` was always reported despite the field existing.
- Hardcoded `your-org/archilens` link in diff markdown output replaced with correct URL.

---

## [0.1.0] — 2026-05-17

### Added
- Initial release
- Core static analysis pipeline (`archilens analyze`)
- L0–L3 Mermaid diagram generation
- AI-powered module summaries and process flow inference
- `archilens diff` for architecture drift detection
- `archilens serve` for interactive web viewer
- `archilens init` for config scaffolding
- GitHub Action composite workflow
- `.archilens.yml` configuration schema

[Unreleased]: https://github.com/saurabh-oss/archilens/compare/v0.16.0...HEAD
[0.16.0]: https://github.com/saurabh-oss/archilens/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/saurabh-oss/archilens/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/saurabh-oss/archilens/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/saurabh-oss/archilens/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/saurabh-oss/archilens/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/saurabh-oss/archilens/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/saurabh-oss/archilens/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/saurabh-oss/archilens/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/saurabh-oss/archilens/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/saurabh-oss/archilens/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/saurabh-oss/archilens/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/saurabh-oss/archilens/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/saurabh-oss/archilens/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/saurabh-oss/archilens/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/saurabh-oss/archilens/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/saurabh-oss/archilens/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/saurabh-oss/archilens/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/saurabh-oss/archilens/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/saurabh-oss/archilens/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/saurabh-oss/archilens/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/saurabh-oss/archilens/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/saurabh-oss/archilens/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/saurabh-oss/archilens/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/saurabh-oss/archilens/releases/tag/v0.1.0

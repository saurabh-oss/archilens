# ArchiLens

**AI-powered layered architecture & process flow visualization for Git repositories.**

ArchiLens analyses your codebase and generates interactive, hierarchical architecture diagrams — from system-level context down to runtime process flows. Think of it as **Google Maps for codebases**: zoom from continents (system architecture) to cities (class-level components).

🌐 **Website**: https://saurabh-oss.github.io/archilens/  
📦 **Repository**: https://github.com/saurabh-oss/archilens  
🧩 **GitHub Action**: https://github.com/marketplace/actions/archilens-architecture-analysis

<p align="center">
  <img src="https://raw.githubusercontent.com/saurabh-oss/archilens/master/docs/images/pr-comment-ai-review.png" width="760"
       alt="ArchiLens comment on a pull request: an AI summary of the architectural change, points worth a look, and a diagram of the modules and dependencies the PR changed">
</p>
<p align="center"><sub>A real ArchiLens comment, on this repository's
<a href="https://github.com/saurabh-oss/archilens/pull/23">PR #23</a>: the AI review, then the modules and
dependencies the PR changed. <a href="https://raw.githubusercontent.com/saurabh-oss/archilens/master/docs/images/pr-comment-full.png">The whole comment</a>.</sub></p>

---

## How ArchiLens Compares

| Capability | CodeBoarding | GitDiagram | **ArchiLens** |
|---|---|---|---|
| How the architecture is derived | Static analysis + LLM | LLM | **Static analysis (Tree-sitter); LLM optional** |
| Runs with no LLM at all | ✗ | ✗ | **✓** |
| Hierarchical drill-down | ✓ (nested components) | ✗ | **✓ (L0 → L3)** |
| Architecture diff posted on PRs | ✓ | ✗ | **✓** |
| Fail CI on declared architecture rules | ✗ | ✗ | **✓** (layer and fan-out rules) |
| Business capability mapping | ✗ | ✗ | **✓** (declared in config) |
| Evolution across git tags | ✗ | ✗ | **✓** |
| Output formats | Mermaid | Mermaid, PNG | **Mermaid, D2, PlantUML** |
| Interactive viewer | ✓ | ✓ | ✓ (local) |
| MCP server for coding agents | ✓ | ✓ (hosted) | ✓ (local) |
| Process flow / sequence diagrams | ✗ | ✗ | ✓ (AI-inferred) |

<sub>Based on each project's README and docs as checked in October 2026. ✗ means we found no mention of the
capability. Corrections welcome — please open an issue.</sub>

> **Language support:** imports are resolved to files in the repository for Python (flat and `src/`
> layouts, relative imports), JavaScript/TypeScript (relative paths, `tsconfig` path aliases, npm/yarn/pnpm
> workspace packages), Java (packages, wildcard and static imports) and Go (module paths). Other languages
> are discovered and counted, but get no dependency edges yet.

---

## Diagram Levels

```
L0: System Context         ← Your system as a black box + external actors
L1: Module Architecture    ← Major modules/services + dependencies
L2: Component Detail       ← Classes, interfaces inside a module
L3: Process Flows          ← Runtime request lifecycles (AI-inferred)
```

---

## Quick Start

### Prerequisites

- Python 3.10+
- Git
- (Optional) Anthropic API key for AI features

### Installation

```bash
# From PyPI: static analysis, diagrams, diff, rules, history (no LLM, ~31 MB)
pip install archilens

# Optional extras
pip install "archilens[ai]"        # AI summaries, process flows, patterns with Claude
pip install "archilens[litellm]"   # other AI providers (OpenAI, Ollama, ...) via LiteLLM
pip install "archilens[viewer]"    # interactive web viewer
pip install "archilens[mcp]"       # MCP server for coding agents

# From source (development)
git clone https://github.com/saurabh-oss/archilens.git
cd archilens
python -m pip install -e ".[dev,viewer,mcp,ai]"
```

> **Windows note**: If `pip` is blocked by policy, use `python -m pip install` instead.

### Initialize in Your Repository

```bash
cd /path/to/your/repo
python -m archilens init
```

This writes a `.archilens.yml` for your repository. It runs a quick static discovery first, so the file
names the languages and modules it found. Everything active in it works as is; capabilities, external
systems and module rules are commented examples built from your real module paths. Edit it to:
- Map modules to business capabilities
- Define entry points for process flow tracing
- Configure external systems for the L0 context diagram
- Set architecture rules for CI enforcement

### Generate Diagrams

```bash
# Full analysis, with AI summaries if the `ai` extra is installed and Anthropic
# credentials are available (ANTHROPIC_API_KEY or `ant auth login`). Otherwise
# ArchiLens prints one warning saying what's missing and runs the static analysis.
python -m archilens analyze --repo .

# Static analysis only, never calls an LLM
python -m archilens analyze --repo . --no-ai

# Generate only a specific level
python -m archilens analyze --repo . --level 1

# Choose output format: mermaid (default), d2, or plantuml
python -m archilens analyze --repo . --format d2

# Output as JSON (for programmatic use)
python -m archilens analyze --repo . --json-output > snapshot.json
```

### Interactive Web Viewer

```bash
python -m archilens serve --repo . --port 8765
```

Requires the `viewer` extra (`pip install "archilens[viewer]"`). Opens a dark-themed single-page app at
`http://127.0.0.1:8765`. It binds to loopback by default; `--host 0.0.0.0` exposes it to your network, and
the viewer has no authentication, so only do that on a trusted network. It includes:
- Sidebar navigation across all four diagram levels
- **Click any module** in the L1 diagram to drill into its L2 component view
- **Zoom controls** (−/+/Fit/1:1), **mouse-wheel zoom**, and **click-drag pan**
- Search/filter sidebar for large repos
- Live re-analysis without restarting the server

### Architecture Drift Detection

```bash
# Compare current branch against main
python -m archilens diff --base main --head HEAD

# Write the diff report to a file
python -m archilens diff --base v1.0 --head v2.0 --output drift.md

# Open the report with an AI review (needs the ai extra and an API key)
python -m archilens diff --base main --head HEAD --ai
```

With `--ai`, the report opens with a few sentences on what the change does to the architecture, up to
four points worth a reviewer's attention, and a one-line description of each new module in the change
table. If the change adds an entry-point file, its process flow is included as a collapsed sequence
diagram. That is one request, plus one per new entry point (at most two). The tables and rule results
are computed exactly as without `--ai`, and without credentials, or if the request fails, the report is
the static diff and a warning is printed.

### Evolution Timeline

```bash
# Semver tags (or evolution.baseline_refs), then HEAD if it is past the last tag
python -m archilens history --repo .

# Specific refs, in the order given
python -m archilens history --repo . --refs "v1.0,v1.5,v2.0,HEAD"
```

Each ref is analysed exactly like `archilens analyze` (module layout, import resolution), straight from
the git object database, so the working tree is never touched. The output is a Mermaid timeline and a
markdown report with modules, classes, module dependencies and LOC per ref, which modules and which
module dependencies appeared or disappeared at each ref, and the overall change from first to last.

### MCP Server for AI Coding Agents

ArchiLens can serve its architecture model to coding agents (Claude Code, Cursor and other MCP clients),
so an agent can check where code belongs and whether a new import is allowed *before* it edits.

```bash
pip install "archilens[mcp]"

# Register with Claude Code (stdio transport)
claude mcp add archilens -- archilens serve-mcp --repo /path/to/repo

# Or run as an HTTP/SSE endpoint (binds to 127.0.0.1 by default)
archilens serve-mcp --repo /path/to/repo --transport sse --port 8766
```

| Tool | What it answers |
|---|---|
| `get_architecture_context(scope)` | Compact module map (`L1`), external systems (`L0`), one module's classes (`L2:<module>`), or capability groupings |
| `get_module_context(file_path)` | Which module owns a file, its capability, size, dependencies and dependants |
| `check_dependency_allowed(source_file, target_module)` | Whether a new import would break a rule: a forbidden dependency (by path or capability), a new cycle, or a fan-out limit |
| `find_capability_owner(feature_description)` | Which module and capability a feature most likely belongs to |

The server re-analyses automatically when the repository changes: a new commit, uncommitted edits,
a change to `.archilens.yml`, or an ArchiLens upgrade. Snapshots of clean work trees are cached in
`~/.archilens/snapshots.db`; `--refresh` clears the cache for the repository.

---

## Configuration Reference

The `.archilens.yml` file controls all analysis behaviour.

Working examples for well-known projects live in
[`docs/configs/`](docs/configs/) — currently a Spring example written
against spring-petclinic, showing one way to name capabilities and rules
for a real codebase.

### Project

```yaml
project:
  name: "My Application"
  description: "What this system does"
  type: "microservices"  # monolith | microservices | modular-monolith | library
```

### Analysis

```yaml
analysis:
  languages: ["python", "typescript"]  # Auto-detected if omitted

  entry_points:   # optional: replaces the detection described below
    - pattern: "**/*controller*.py"
      type: "http_handler"
    - pattern: "**/routes/**/*.ts"
      type: "http_handler"

  exclude:
    - "**/node_modules/**"
    - "**/__pycache__/**"
    - "**/venv/**"

  module_depth: 2   # directory levels below a source root that name a module
```

#### How files are grouped into modules

A module is a directory. ArchiLens first finds the *source roots*: the repository root, Python source
roots such as `src/`, JVM roots such as `src/main/java`, directories with a `go.mod`, and JS/TS workspace
packages (directories with their own `package.json`). Below a root it skips directories that contain no
files and only one subdirectory, such as Java's `com/acme/shop` or a lone `src/<package>`, and the next
`module_depth` levels name the module. Each workspace package is a single module.

| File | Module |
|---|---|
| `app/orders/service.py` | `app/orders` |
| `src/shop/payments/gateway.py` | `src/shop/payments` |
| `src/main/java/com/acme/shop/orders/OrderService.java` | `src/main/java/com/acme/shop/orders` |
| `packages/core/injector/container.ts` (in a workspace) | `packages/core` |

Files ignored by `.gitignore`, and files under `test/`, `tests/`, `__tests__/`, `spec/`, `e2e/` or
`testdata/`, are not part of any module.

Example apps, documentation and benchmarks at the repository root (`examples/`, `example/`,
`samples/`, `sample/`, `demo/`, `demos/`, `docs/`, `docs_src/`, `benchmarks/`, `benchmark/`) are excluded
by default: they would otherwise crowd the module diagram (Express ships 26 example apps beside one
library). Only the root counts, so a Java package such as `com/example/demo` is analysed as usual.
`archilens init` also leaves out root `integration/`, `scripts/`, `tools/`, `fixtures/` and
`playground/` folders it finds, with a comment on each line in the config it writes; delete a line to
analyse that folder. Setting `analysis.exclude` replaces the default list.

### Business Capability Mapping

```yaml
capabilities:
  - name: "Order Management"
    description: "Order lifecycle from creation to fulfillment"
    modules:
      - "src/orders/**"
      - "services/order-service/**"

  - name: "Payment Processing"
    modules:
      - "src/payments/**"
```

### External Systems

```yaml
external_systems:
  - name: "PostgreSQL"
    type: "database"
    description: "Primary data store"

  - name: "Stripe API"
    type: "external_api"
    description: "Payment processing"
```

### Diagram Output

```yaml
diagrams:
  output_dir: ".archilens/diagrams"
  format: "mermaid"   # mermaid | d2 | plantuml
  l1_max_edges: 40     # most dependencies drawn on L1, heaviest first; 0 draws all
  levels:
    - { level: 0, enabled: true }
    - { level: 1, enabled: true }
    - { level: 2, enabled: true }
    - { level: 3, enabled: true }
```

**Readable module diagrams on large projects.** On L1, line width shows how many imports a
dependency carries (1px for one, up to 4px for the heaviest in that diagram, on a log scale) instead
of an "N imports" label on every arrow. Above `l1_max_edges` dependencies, L1 draws the heaviest ones,
after first giving every module its strongest dependency so nothing looks unconnected, and says how
many it left out. The Mermaid output lists every dependency, with its import count, in a collapsible
table under the diagram. Django 6.1 has 17 modules and 109 dependencies; its L1 draws 40.

### AI Configuration

```yaml
ai:
  provider: "anthropic"        # anthropic | openai | ollama | litellm
  model: "claude-opus-5-5"     # cheaper option for one-line summaries: "claude-haiku-5-5"
  effort: "low"                # Claude 5 models: low | medium | high | xhigh | max (null = model default)
  cache: true                  # reuse answers for unchanged input (~/.archilens/ai_cache.db)
  features:
    process_flow_inference: true
    node_annotations: true         # reserved, not used yet
    capability_suggestions: true   # reserved, not used yet
    pattern_detection: true
    module_summaries: true
```

**What is sent to the model.**
- *Module summaries*: each module's name, class names and dependencies, plus a signature outline of its
  largest files (class, function and type declarations only, no bodies, capped at about 6,000 characters).
- *Process flows*: the source of each entry-point file, truncated to 15,000 characters.
- *Pattern detection*: the module dependency list and a directory tree.
- *Diff review* (`diff --ai`): the change table, new rule violations, the list of changed files, and a
  signature outline of each new module.

**Entry points.** Process flows start from entry points, one AI request each. If `analysis.entry_points`
is not set, ArchiLens detects up to 5, in this order:

1. Console scripts in `pyproject.toml` (`[project.scripts]`, `[project.gui-scripts]`,
   `[tool.poetry.scripts]`), and `bin` and `main` in `package.json`. A target only counts if it is a
   source file in the repository, so a `bin` pointing at build output is skipped.
2. File names: `main.go`, `*Application.java` / `.kt`, `main.py`, `app.py`, `server.py`, `manage.py`,
   `wsgi.py`, `asgi.py`, `__main__.py`, and `main`, `index`, `server` or `app` `.js` / `.ts` at the
   root of a package or in its `src/`.

`analyze` logs which files it picked. List entry points under `analysis.entry_points` to choose
them yourself; that replaces the detection.

Answers are cached by a hash of the model, effort, schema and prompt, so unchanged modules cost nothing on
the next run and their summaries don't change wording.

**What it used.** `analyze` logs the AI usage of each run, and the `diff --ai` report credits it under
the summary, e.g. *AI usage: 1 request: 1,039 input and 275 output tokens*, or *answered from cache*.
Input includes prompt-cache reads and writes, and output includes thinking. ArchiLens reports tokens
rather than dollars because prices change; see [Anthropic's pricing](https://www.anthropic.com/pricing)
for your model.

For modules with no configured capability, the
AI's capability suggestion is shown in `analyze` output and the MCP `get_module_context` tool as a
*suggestion*; it is never assigned, because capabilities drive CI rules.

### CI Rules

```yaml
ci:
  fail_on: "new"            # new (default): only violations a PR introduces fail | all
  rules:
    - name: "ui-skips-domain"
      type: "forbidden-dependency"
      from: "src/presentation/**"
      to: "src/infrastructure/**"
      action: "fail"        # warn | fail

    - name: "payments-independent-of-identity"
      type: "forbidden-dependency"
      from_capability: "Payment Processing"   # capabilities come from the `capabilities` section
      to_capability: "User Management"
      action: "fail"

    - name: "acyclic-modules"
      type: "no-cycles"
      action: "warn"

    - name: "small-surface"
      type: "max-fan-out"
      threshold: 10
      action: "warn"
```

| Type | Fires when |
|---|---|
| `forbidden-dependency` | A module matching the `from` side depends on a module matching the `to` side. Each side selects modules by path glob (`from` / `to`), by capability (`from_capability` / `to_capability`), or both, in which case both must match. |
| `no-cycles` | Modules depend on each other in a cycle (reported once per cycle). |
| `max-fan-out` | A module depends on more than `threshold` other modules. |

Patterns are matched against module paths, and `dir/**` also matches `dir` itself. A rule with an
unknown or missing `type`, or without the fields its type needs, is a configuration error. (Rules named
`no-layer-skip` or `max-fan-out` without a `type` keep working, as in earlier versions.)

**Existing violations don't fail pull requests.** `archilens diff` evaluates the rules on both refs:
violations already present on the base ref are listed as *existing* (folded in the PR comment), and only
new ones count. So a rule can be adopted on a codebase that breaks it today, and PRs that make things
worse still fail. Set `fail_on: "all"` to fail on every violation of a `fail` rule.

To check the working tree without a diff (locally, or in a pre-commit hook):

```bash
archilens check --repo .            # exit 1 if any rule with action: "fail" is violated
archilens check --repo . --json-output
```

---

## GitHub Actions Integration

### Using the ArchiLens action

The action is listed on the [GitHub Marketplace](https://github.com/marketplace/actions/archilens-architecture-analysis). The composite action runs the analysis, posts (and on later pushes, updates) one architecture-diff
comment per pull request, and uploads the diagrams as a workflow artifact. The comment opens with a
Mermaid diagram of just the modules and dependencies the PR changes (new, removed, resized, and any new
dependency that breaks a rule), followed by the full change table and rule results. With
`enable-ai: "true"` it opens with an AI review instead (see `diff --ai` above):

```yaml
# .github/workflows/architecture.yml
name: Architecture
on:
  pull_request:

permissions:
  contents: read
  pull-requests: write   # to post the PR comment

jobs:
  archilens:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0   # the diff needs the base commit
      - uses: saurabh-oss/archilens@v0.15.1
        with:
          fail-on-violations: "true"   # fail the job on rules with action: "fail"
          # Optional AI review in the PR comment, billed to your own key:
          # enable-ai: "true"
          # anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

| Input | Default | Meaning |
|---|---|---|
| `repo-path` | `.` | Repository to analyse |
| `install` | `archilens` | What to `pip install`: pin a release such as `archilens==0.15.1` (recommended) |
| `enable-ai` | `false` | `"true"` opens the PR comment with an AI review. One request per push, plus one per entry-point file the PR adds (at most two). Needs `anthropic-api-key`; rule checks and `fail-on-violations` never use AI |
| `ai-diagrams` | `false` | With `enable-ai`, `"true"` also adds AI module summaries, process flows and patterns to the uploaded diagrams: about one request per module on every run |
| `anthropic-api-key` | — | Passed as `ANTHROPIC_API_KEY`. Store it as a secret in your own repository: usage is billed to your key, and GitHub doesn't pass secrets to PRs from forks |
| `comment-on-pr` | `true` | Post or update the diff comment |
| `fail-on-violations` | `false` | Fail the job when the PR introduces a violation of a rule with `action: "fail"` |
| `diagrams-output` | `.archilens/diagrams` | Where diagrams are written |

With `enable-ai`, the action keeps ArchiLens's AI answer cache between runs (`actions/cache`), so a
re-run, or a push that doesn't change what a request contains, costs nothing.

[Here is a complete comment](https://raw.githubusercontent.com/saurabh-oss/archilens/master/docs/images/pr-comment-full.png), as the action posted it on PR #23.

Outputs: `diagrams-path`, `has-changes`, `violations-count`. The report is also written to the job
summary. On pull requests from forks, GitHub gives the workflow a read-only token, so the comment can't be
posted; the action logs a warning and carries on.

Workflows written for 0.8.0 and earlier as `uses: saurabh-oss/archilens/github_action@<version>` keep
working: that path holds an identical copy of the action.

### Writing the workflow yourself

Or add this workflow to your repository:

```yaml
# .github/workflows/archilens.yml
name: Architecture Analysis
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: write
  pull-requests: write

jobs:
  architecture:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - run: pip install "archilens[ai]"

      - name: Generate Diagrams
        if: github.event_name == 'push'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python -m archilens analyze --repo . --output .archilens/diagrams

      - name: Commit Diagrams
        if: github.event_name == 'push'
        run: |
          git config user.name "ArchiLens Bot"
          git config user.email "archilens[bot]@users.noreply.github.com"
          git add .archilens/diagrams/
          git diff --cached --quiet || git commit -m "docs: update architecture diagrams [skip ci]"
          git push

      - name: PR Drift Detection
        if: github.event_name == 'pull_request'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python -m archilens diff --base origin/${{ github.base_ref }} --head HEAD
```

---

## Architecture of ArchiLens Itself

```
archilens/
├── archilens/                    # Core Python package
│   ├── __init__.py
│   ├── __main__.py               # Enables python -m archilens
│   ├── cli.py                    # Click CLI with Rich output (the only top-level module)
│   ├── config/                   # YAML config loader, defaults and validation
│   ├── engine/                   # Main orchestrator pipeline
│   ├── rules/                    # Architecture rule engine (forbidden deps, cycles, fan-out)
│   ├── models/
│   │   └── __init__.py           # Pydantic models (ArchSnapshot, nodes, edges, flows)
│   ├── analyzers/
│   │   ├── discovery.py          # File discovery + language detection
│   │   ├── dependencies.py       # Static analysis (imports, classes, call graph)
│   │   ├── treesitter.py         # Tree-sitter AST extraction (Python/JS/TS/Java/Go)
│   │   ├── diff.py               # Architecture diff + rule results between two refs
│   │   ├── evolution.py          # Git history evolution & timeline
│   │   └── git_utils.py          # Git ref checkout via blob reading
│   ├── generators/
│   │   ├── base.py               # DiagramGenerator ABC + factory
│   │   ├── mermaid.py            # Mermaid.js output (L0-L3)
│   │   ├── d2.py                 # D2 output (L0-L3)
│   │   ├── plantuml.py           # PlantUML output (L0-L3)
│   │   └── diff_report.py        # The diff report / PR comment (Markdown + Mermaid)
│   ├── viewer/
│   │   ├── __init__.py
│   │   └── app.py                # Flask SPA with zoom/pan, drill-down
│   ├── mcp/
│   │   ├── server.py             # MCP server: architecture tools for coding agents
│   │   └── cache.py              # SQLite snapshot cache
│   ├── ai/
│   │   └── __init__.py           # LLM integration (Anthropic/OpenAI/Ollama)
│   ├── utils/
│   │   └── __init__.py           # Module glob matching
│   └── templates/
│       └── archilens.example.yml # Annotated config written by `archilens init`
├── docs/
│   └── index.html                # GitHub Pages landing site
├── scripts/
│   └── render_check.py           # Renders every diagram format with the official tools (CI)
├── action.yml                    # Composite GitHub Action (the Marketplace listing)
├── github_action/
│   └── action.yml                # Identical copy for the older `.../github_action@` path
├── tests/                        # pytest suite (CLI, analysis, generators, MCP, viewer)
├── .archilens.yml                # ArchiLens's own architecture rules, checked on every PR
├── .github/workflows/
│   ├── ci.yml                    # Tests, Ruff, mypy
│   ├── publish.yml               # PyPI release on v*.*.* tags
│   └── archilens.yml             # Runs the ArchiLens action on ArchiLens's own PRs
├── pyproject.toml                # Project metadata + dependencies
└── README.md
```

### Pipeline Flow

```
  .archilens.yml          Source Code           Git History
       │                      │                      │
       ▼                      ▼                      ▼
  ┌──────────┐      ┌──────────────────┐     ┌────────────┐
  │  Config   │      │    Discovery     │     │  Git Utils │
  │  Loader   │      │  (Tree-sitter)   │     │ (evolution │
  └────┬─────┘      └───────┬──────────┘     │  + diff)   │
       │                    │                └─────┬──────┘
       ▼                    ▼                      │
  ┌─────────────────────────────────────────────────────────┐
  │                    Analysis Engine                       │
  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
  │  │   Static    │  │     AI       │  │   Metrics &   │  │
  │  │  Analysis   │  │  Augmentation│  │   Patterns    │  │
  │  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │
  │         └────────┬────────┘──────────────────┘          │
  └──────────────────┼──────────────────────────────────────┘
                     ▼
              ┌──────────────┐
              │ ArchSnapshot │  (Pydantic: nodes + edges + flows)
              └──────┬───────┘
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
  ┌─────────┐  ┌──────────┐  ┌──────────┐
  │Mermaid  │  │    D2    │  │ PlantUML │
  │  (L0-3) │  │  (L0-3)  │  │  (L0-3)  │
  └────┬────┘  └──────────┘  └──────────┘
       │
       ▼
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ Web      │   │   Diff   │   │   JSON   │
  │ Viewer   │   │  Report  │   │  Export  │
  └──────────┘   └──────────┘   └──────────┘
```

---

## Development Setup

```bash
# Clone the repository
git clone https://github.com/saurabh-oss/archilens.git
cd archilens

# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows

# Install with dev dependencies (viewer and mcp extras are needed by the tests and mypy)
python -m pip install -e ".[dev,viewer,mcp,ai]"

# Run tests
pytest tests/ -v --cov=archilens

# Lint
ruff check archilens/
ruff format --check archilens/
mypy archilens/
```

---

## Roadmap

- [x] Core static analysis engine (multi-language via Tree-sitter + regex fallback)
- [x] L0-L3 Mermaid diagram generation
- [x] D2 and PlantUML output formats
- [x] AI-powered process flow inference and module summaries
- [x] Architecture drift detection (diff engine with CI rules)
- [x] Git history evolution timeline (`archilens history`)
- [x] GitHub Action for CI/CD
- [x] Business capability mapping
- [x] CLI with Rich output (`analyze`, `diff`, `check`, `history`, `serve`, `serve-mcp`, `init`)
- [x] MCP server for AI coding agents (`archilens serve-mcp`)
- [x] Interactive web viewer with zoom/pan and click-to-drill-down
- [x] GitHub Pages landing site
- [x] PyPI release (`pip install archilens`)
- [x] Per-language import resolution: JS/TS relative and path-alias imports, workspace packages, Java packages, Go module paths, Python `src/` layouts
- [x] Rule types decoupled from rule names, capability-level rules, and a violation baseline so only new violations fail CI
- [ ] VS Code extension
- [ ] GitHub App (persistent bot with richer PR integration)
- [ ] Monorepo support (multi-service cross-repo analysis)
- [ ] OpenTelemetry integration (runtime architecture from traces)

---

## Contributing

Contributions are welcome! Key areas:

1. **Language parsers** — Add Tree-sitter grammars for more languages (Ruby, Rust, C#, etc.)
2. **Pattern detection** — Expand the set of recognized architectural patterns (CQRS, Saga, etc.)
3. **VS Code extension** — TypeScript extension surfacing L2/L3 diagrams inline
4. **GitHub App** — Probot/Node.js app for persistent PR bot integration

---

## Getting help

- **Questions:** [Discussions](https://github.com/saurabh-oss/archilens/discussions)
- **Bugs and feature requests:** [Issues](https://github.com/saurabh-oss/archilens/issues/new/choose)
- **Security problems:** report privately, see [SECURITY.md](SECURITY.md)

## License

MIT — see [LICENSE](LICENSE).

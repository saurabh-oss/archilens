# ArchiLens

**AI-powered layered architecture & process flow visualization for Git repositories.**

ArchiLens analyses your codebase and generates interactive, hierarchical architecture diagrams — from system-level context down to runtime process flows. Think of it as **Google Maps for codebases**: zoom from continents (system architecture) to cities (class-level components).

🌐 **Website**: https://saurabh-oss.github.io/archilens/  
📦 **Repository**: https://github.com/saurabh-oss/archilens

---

## What Makes ArchiLens Different

| Capability | Swark | GitDiagram | CodeBoarding | **ArchiLens** |
|---|---|---|---|---|
| Hierarchical drill-down (L0→L3) | ✗ | ✗ | Partial | **✓** |
| Process flow / sequence diagrams | ✗ | ✗ | ✗ | **✓ (AI)** |
| GitHub-native (Action + PR comments) | ✗ | ✗ | ✓ | **✓** |
| Architecture drift detection in CI | ✗ | ✗ | ✗ | **✓** |
| Business capability mapping | ✗ | ✗ | ✗ | **✓** |
| Git history evolution view | ✗ | ✗ | ✗ | **✓** |
| Multi-format output (Mermaid/D2/PlantUML) | ✗ | ✗ | ✗ | **✓** |
| Multi-language support | ✓ (LLM) | ✓ (LLM) | ✓ | **✓ (Tree-sitter)** |
| Interactive web viewer | ✗ | ✗ | ✗ | **✓** |
| Works without API keys | ✗ | ✗ | ✗ | **✓ (static only)** |

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
# From GitHub
pip install git+https://github.com/saurabh-oss/archilens.git

# From source (development)
git clone https://github.com/saurabh-oss/archilens.git
cd archilens
python -m pip install -e ".[dev]"
```

> **Windows note**: If `pip` is blocked by policy, use `python -m pip install` instead.

### Initialize in Your Repository

```bash
cd /path/to/your/repo
python -m archilens init
```

This creates a `.archilens.yml` config file. Edit it to:
- Map modules to business capabilities
- Define entry points for process flow tracing
- Configure external systems for the L0 context diagram
- Set architecture rules for CI enforcement

### Generate Diagrams

```bash
# Full analysis with AI (requires ANTHROPIC_API_KEY env var)
python -m archilens analyze --repo .

# Static analysis only (no API key needed)
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

Opens a dark-themed single-page app at `http://localhost:8765` with:
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
```

### Evolution Timeline

```bash
# Analyse architecture across all semver tags
python -m archilens history --repo . --tags

# Analyse specific refs
python -m archilens history --repo . --refs "v1.0,v1.5,v2.0,HEAD"
```

Produces a Mermaid timeline diagram and a full markdown evolution report showing module additions, removals, and LOC trends across your git history.

---

## Configuration Reference

The `.archilens.yml` file controls all analysis behaviour.

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

  entry_points:
    - pattern: "**/*controller*.py"
      type: "http_handler"
    - pattern: "**/routes/**/*.ts"
      type: "http_handler"

  exclude:
    - "**/node_modules/**"
    - "**/__pycache__/**"
    - "**/venv/**"

  max_depth: 5
```

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
  levels:
    - { level: 0, enabled: true }
    - { level: 1, enabled: true }
    - { level: 2, enabled: true }
    - { level: 3, enabled: true }
```

### AI Configuration

```yaml
ai:
  provider: "anthropic"        # anthropic | openai | ollama | litellm
  model: "claude-sonnet-4-6"
  features:
    process_flow_inference: true
    node_annotations: true
    capability_suggestions: true
    pattern_detection: true
    module_summaries: true
```

### CI Rules

```yaml
ci:
  drift_detection: true
  pr_comments: true
  rules:
    - name: "no-layer-skip"
      from: "src/presentation/**"
      to: "src/infrastructure/**"
      action: "warn"

    - name: "max-fan-out"
      threshold: 10
      action: "fail"
```

---

## GitHub Actions Integration

Add this workflow to your repository:

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
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install git+https://github.com/saurabh-oss/archilens.git

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
│   ├── cli.py                    # Click CLI with Rich output
│   ├── config.py                 # YAML config loader + defaults
│   ├── engine.py                 # Main orchestrator pipeline
│   ├── models/
│   │   └── __init__.py           # Pydantic models (ArchSnapshot, nodes, edges, flows)
│   ├── analyzers/
│   │   ├── discovery.py          # File discovery + language detection
│   │   ├── dependencies.py       # Static analysis (imports, classes, call graph)
│   │   ├── treesitter.py         # Tree-sitter AST extraction (Python/JS/TS/Java/Go)
│   │   ├── diff.py               # Architecture diff engine
│   │   ├── evolution.py          # Git history evolution & timeline
│   │   └── git_utils.py          # Git ref checkout via blob reading
│   ├── generators/
│   │   ├── base.py               # DiagramGenerator ABC + factory
│   │   ├── mermaid.py            # Mermaid.js output (L0-L3)
│   │   ├── d2.py                 # D2 output (L0-L3)
│   │   └── plantuml.py           # PlantUML output (L0-L3)
│   ├── viewer/
│   │   ├── __init__.py
│   │   └── app.py                # Flask SPA with zoom/pan, drill-down
│   ├── ai/
│   │   └── __init__.py           # LLM integration (Anthropic/OpenAI/Ollama)
│   └── utils/
│       └── __init__.py
├── docs/
│   └── index.html                # GitHub Pages landing site
├── github_action/
│   └── action.yml                # Composite GitHub Action
├── tests/
│   ├── test_core.py              # Core analysis tests
│   └── test_new_features.py      # Tree-sitter, generators, viewer, diff, evolution
├── .archilens.yml                # Example configuration
├── .github/workflows/
│   └── archilens.yml             # Example CI workflow
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

# Install with dev dependencies
python -m pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=archilens

# Lint
ruff check archilens/
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
- [x] CLI with Rich output (`analyze`, `diff`, `history`, `serve`, `init`)
- [x] Interactive web viewer with zoom/pan and click-to-drill-down
- [x] GitHub Pages landing site
- [ ] VS Code extension
- [ ] GitHub App (persistent bot with richer PR integration)
- [ ] Monorepo support (multi-service cross-repo analysis)
- [ ] OpenTelemetry integration (runtime architecture from traces)
- [ ] PyPI release

---

## Contributing

Contributions are welcome! Key areas:

1. **Language parsers** — Add Tree-sitter grammars for more languages (Ruby, Rust, C#, etc.)
2. **Pattern detection** — Expand the set of recognized architectural patterns (CQRS, Saga, etc.)
3. **VS Code extension** — TypeScript extension surfacing L2/L3 diagrams inline
4. **GitHub App** — Probot/Node.js app for persistent PR bot integration

---

## License

MIT — see [LICENSE](LICENSE).

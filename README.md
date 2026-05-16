# ArchiLens

**AI-powered layered architecture & process flow visualization for Git repositories.**

ArchiLens analyzes your codebase and generates interactive, hierarchical architecture diagrams — from system-level context down to runtime process flows. Think of it as **Google Maps for codebases**: zoom from continents (system architecture) to cities (class-level components).

---

## What Makes ArchiLens Different

| Capability | Swark | GitDiagram | CodeBoarding | **ArchiLens** |
|---|---|---|---|---|
| Hierarchical drill-down (L0→L3) | ✗ | ✗ | Partial | **✓** |
| Process flow / sequence diagrams | ✗ | ✗ | ✗ | **✓ (AI)** |
| GitHub-native (Action + PR comments) | ✗ | ✗ | ✓ | **✓** |
| Architecture drift detection in CI | ✗ | ✗ | ✗ | **✓** |
| Business capability mapping | ✗ | ✗ | ✗ | **✓** |
| Git history evolution view | ✗ | ✗ | ✗ | **Planned** |
| Multi-language support | ✓ (LLM) | ✓ (LLM) | ✓ | **✓ (Tree-sitter)** |
| Works without API keys | ✗ | ✗ | ✗ | **✓ (static only)** |

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
# From PyPI (when published)
pip install archilens

# From source (development)
git clone https://github.com/your-org/archilens.git
cd archilens
pip install -e ".[dev]"
```

### Initialize in Your Repository

```bash
cd /path/to/your/repo
archilens init
```

This creates a `.archilens.yml` config file. Edit it to:
- Map modules to business capabilities
- Define entry points for process flow tracing
- Configure external systems for the L0 context diagram
- Set architecture rules for CI enforcement

### Generate Diagrams

```bash
# Full analysis with AI (requires ANTHROPIC_API_KEY env var)
archilens analyze --repo .

# Static analysis only (no API key needed)
archilens analyze --repo . --no-ai

# Generate only a specific level
archilens analyze --repo . --level 1

# Output as JSON (for programmatic use)
archilens analyze --repo . --json-output > snapshot.json
```

### Architecture Drift Detection

```bash
# Compare current branch against main
archilens diff --base main --head HEAD
```

---

## Configuration Reference

The `.archilens.yml` file controls all analysis behavior. Here's the structure:

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

### AI Configuration

```yaml
ai:
  provider: "anthropic"        # anthropic | openai | ollama | litellm
  model: "claude-sonnet-4-20250514"
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

      - run: pip install archilens

      - name: Generate Diagrams
        if: github.event_name == 'push'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: archilens analyze --repo . --output .archilens/diagrams

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
        run: archilens diff --base origin/${{ github.base_ref }} --head HEAD
```

---

## Architecture of ArchiLens Itself

```
archilens/
├── archilens/                  # Core Python package
│   ├── __init__.py
│   ├── cli.py                  # Click CLI with Rich output
│   ├── config.py               # YAML config loader + defaults
│   ├── engine.py               # Main orchestrator pipeline
│   ├── models/
│   │   └── __init__.py         # Pydantic data models (ArchSnapshot, nodes, edges)
│   ├── analyzers/
│   │   ├── discovery.py        # File discovery + language detection
│   │   ├── dependencies.py     # Static analysis (imports, classes, call graph)
│   │   └── diff.py             # Architecture diff engine
│   ├── generators/
│   │   └── mermaid.py          # Mermaid.js diagram output (L0-L3)
│   ├── ai/
│   │   └── __init__.py         # LLM integration (Anthropic/OpenAI/Ollama)
│   └── utils/
│       └── __init__.py
├── github_action/
│   └── action.yml              # Composite GitHub Action
├── tests/
│   └── test_core.py            # Pytest test suite
├── .archilens.yml              # Example configuration
├── .github/workflows/
│   └── archilens.yml           # Example CI workflow
├── pyproject.toml              # Project metadata + dependencies
└── README.md
```

### Pipeline Flow

```
  .archilens.yml          Source Code           Git History
       │                      │                      │
       ▼                      ▼                      ▼
  ┌──────────┐      ┌──────────────────┐     ┌────────────┐
  │  Config   │      │    Discovery     │     │  Git Diff  │
  │  Loader   │      │  (Tree-sitter)   │     │  Engine    │
  └────┬─────┘      └───────┬──────────┘     └─────┬──────┘
       │                    │                       │
       ▼                    ▼                       ▼
  ┌─────────────────────────────────────────────────────────┐
  │                  Analysis Engine                         │
  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
  │  │   Static    │  │     AI       │  │   Metrics &   │  │
  │  │  Analysis   │  │  Augmentation│  │   Patterns    │  │
  │  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │
  │         └────────┬───────┘──────────────────┘           │
  └──────────────────┼──────────────────────────────────────┘
                     ▼
              ┌──────────────┐
              │ ArchSnapshot │  (Pydantic model: nodes + edges + flows)
              └──────┬───────┘
                     │
          ┌──────────┼───────────┐
          ▼          ▼           ▼
    ┌──────────┐ ┌────────┐ ┌────────┐
    │ Mermaid  │ │  Diff  │ │  JSON  │
    │ Diagrams │ │ Report │ │ Export │
    └──────────┘ └────────┘ └────────┘
```

---

## Development Setup

```bash
# Clone the repository
git clone https://github.com/your-org/archilens.git
cd archilens

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=archilens

# Lint
ruff check archilens/
mypy archilens/
```

---

## Roadmap

- [x] Core static analysis engine (multi-language via regex + Tree-sitter)
- [x] L0-L3 Mermaid diagram generation
- [x] AI-powered process flow inference
- [x] Architecture drift detection (diff engine)
- [x] GitHub Action for CI/CD
- [x] Business capability mapping
- [x] CLI with Rich output
- [ ] Interactive web viewer (React + D3.js drill-down)
- [ ] Git history evolution timeline (animated architecture changes)
- [ ] VS Code extension
- [ ] D2 and PlantUML output formats
- [ ] Tree-sitter AST parsing (replacing regex for higher accuracy)
- [ ] GitHub App (persistent bot with richer integration)
- [ ] Monorepo support (multi-service analysis)
- [ ] OpenTelemetry integration (runtime architecture from traces)

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Key areas where contributions would be valuable:
1. **Language parsers**: Add Tree-sitter grammars for more languages
2. **Pattern detection**: Expand the set of recognized architectural patterns
3. **Diagram formats**: Add D2 or PlantUML output generators
4. **Interactive viewer**: Build the React + D3.js drill-down UI

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

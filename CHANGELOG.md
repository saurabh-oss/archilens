# Changelog

All notable changes to ArchiLens are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- Tree-sitter AST extraction for Python, JavaScript, TypeScript, Java, Go
- Multi-format diagram generators: Mermaid, D2, PlantUML (L0–L3)
- Interactive Flask web viewer with zoom/pan and click-to-drill-down
- Git history evolution timeline (`archilens history`)
- Architecture diff engine with CI rule enforcement (`archilens diff`)
- `python -m archilens` entry point (`__main__.py`)
- GitHub Pages landing site (`docs/index.html`)
- CI workflow running tests on Python 3.10, 3.11, 3.12
- PyPI publish workflow (tag-triggered, trusted publishing)

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

[Unreleased]: https://github.com/saurabh-oss/archilens/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saurabh-oss/archilens/releases/tag/v0.1.0

# Changelog

All notable changes to ArchiLens are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

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

[Unreleased]: https://github.com/saurabh-oss/archilens/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/saurabh-oss/archilens/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/saurabh-oss/archilens/releases/tag/v0.1.0

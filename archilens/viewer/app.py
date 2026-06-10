"""
Flask-based interactive architecture viewer.

Runs the full ArchiLens analysis pipeline on demand and serves an
interactive single-page UI with:
  - Sidebar navigation (L0 → L1 → L2 per module → L3 per flow)
  - Cytoscape.js interactive graph for L1 (supports 500+ nodes, click-to-drill)
  - Mermaid.js for L2 class diagrams and L3 sequence diagrams
  - Search/filter sidebar for large repos

Usage:
    archilens serve --repo /path/to/repo --port 8765
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Capability colour palette (dark-theme friendly)
_CAP_COLORS = [
    "#1f6feb", "#2ea043", "#9e6a03", "#8957e5",
    "#da3633", "#0075ca", "#bf8700", "#5a6e16",
]


def create_app(repo_path: str | Path, use_ai: bool = False) -> "Flask":  # type: ignore[name-defined]  # noqa: F821
    """
    Create and configure the Flask application.

    Args:
        repo_path:  Repository root to analyse.
        use_ai:     Whether to enable AI-powered diagram enrichment.
    """
    try:
        from flask import Flask, jsonify, render_template_string  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("Flask is required for the viewer. Install with: pip install archilens[viewer]") from exc

    from archilens.config import load_config
    from archilens.engine import analyze_repository
    from archilens.generators.mermaid import (
        generate_component_detail,
        generate_module_architecture,
        generate_process_flow,
        generate_system_context,
    )

    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
    repo_path = Path(repo_path).resolve()

    # Cache the snapshot so we don't re-analyse on every request
    _cache: dict[str, object] = {}

    def _get_snapshot():
        if "snapshot" not in _cache:
            config = load_config(repo_path)
            _cache["config"] = config
            _cache["snapshot"] = analyze_repository(repo_path, config, use_ai=use_ai)
        return _cache["snapshot"], _cache["config"]

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        snapshot, config = _get_snapshot()
        return render_template_string(
            _HTML_TEMPLATE,
            project_name=snapshot.project_name,
            git_ref=snapshot.git_ref or "HEAD",
        )

    @app.route("/api/nav")
    def nav():
        """Return the navigation tree as JSON."""
        snapshot, config = _get_snapshot()
        modules = snapshot.get_module_nodes()

        items = [
            {"id": "l0", "label": "L0 System Context", "level": 0},
            {"id": "l1", "label": "L1 Module Architecture", "level": 1},
        ]

        for mod in sorted(modules, key=lambda n: n.name):
            safe = mod.name.lower().replace(" ", "_")
            items.append(
                {
                    "id": f"l2_{safe}",
                    "label": f"  {mod.name}",
                    "level": 2,
                    "module_id": mod.id,
                }
            )

        for flow in snapshot.flows:
            safe = flow.name.lower().replace(" ", "_").replace("/", "_")
            items.append(
                {
                    "id": f"l3_{safe}",
                    "label": f"  {flow.name}",
                    "level": 3,
                    "flow_id": flow.id,
                }
            )

        return jsonify(items)

    # --- Mermaid diagram routes (backward-compatible, used by CLI output) ---

    @app.route("/api/diagram/l0")
    def diagram_l0():
        snapshot, config = _get_snapshot()
        content = generate_system_context(snapshot, config)
        mermaid_code = _extract_mermaid(content)
        return jsonify({"mermaid": mermaid_code, "title": "L0: System Context", "level": 0})

    @app.route("/api/diagram/l1")
    def diagram_l1():
        snapshot, _ = _get_snapshot()
        content = generate_module_architecture(snapshot)
        mermaid_code = _extract_mermaid(content)
        return jsonify({"mermaid": mermaid_code, "title": "L1: Module Architecture", "level": 1})

    @app.route("/api/diagram/l2/<path:module_id>")
    def diagram_l2(module_id: str):
        snapshot, _ = _get_snapshot()
        node = next(
            (
                n
                for n in snapshot.get_module_nodes()
                if n.id == module_id or n.name.lower().replace(" ", "_") == module_id
            ),
            None,
        )
        if node is None:
            return jsonify({"error": "Module not found"}), 404

        content = generate_component_detail(snapshot, node.id)
        if content is None:
            return jsonify({"mermaid": "", "title": f"L2: {node.name}", "level": 2, "empty": True})

        mermaid_code = _extract_mermaid(content)
        return jsonify({"mermaid": mermaid_code, "title": f"L2: {node.name}", "level": 2})

    @app.route("/api/diagram/l3/<path:flow_id>")
    def diagram_l3(flow_id: str):
        snapshot, _ = _get_snapshot()
        flow = next(
            (
                f
                for f in snapshot.flows
                if f.id == flow_id or f.name.lower().replace(" ", "_").replace("/", "_") == flow_id
            ),
            None,
        )
        if flow is None:
            return jsonify({"error": "Flow not found"}), 404

        content = generate_process_flow(flow)
        mermaid_code = _extract_mermaid(content)
        return jsonify({"mermaid": mermaid_code, "title": f"L3: {flow.name}", "level": 3})

    # --- Cytoscape graph route (used by viewer for L1 interactive graph) ---

    @app.route("/api/graph/l1")
    def graph_l1():
        """
        Return L1 module architecture as Cytoscape.js elements JSON.

        Includes compound nodes for capability groups, module nodes sized
        by LOC, and weighted edges.  Suitable for graphs with 500+ nodes.
        """
        snapshot, _ = _get_snapshot()
        modules = snapshot.get_module_nodes()

        cy_nodes = []
        cy_edges = []

        # Capability parent nodes
        cap_index: dict[str, str] = {}
        for i, cap_name in enumerate(snapshot.capability_map):
            cap_id = f"cap__{cap_name.replace(' ', '_')}"
            cap_index[cap_name] = cap_id
            cy_nodes.append({
                "data": {
                    "id": cap_id,
                    "label": cap_name,
                    "type": "capability",
                    "color": _CAP_COLORS[i % len(_CAP_COLORS)],
                },
                "classes": "capability-group",
            })

        # Module nodes
        for node in modules:
            data: dict = {
                "id": node.id,
                "label": node.name,
                "loc": node.lines_of_code,
                "capability": node.capability or "",
                "summary": node.ai_summary or "",
                "type": "module",
            }
            if node.capability and node.capability in cap_index:
                data["parent"] = cap_index[node.capability]
            cy_nodes.append({"data": data, "classes": "module-node"})

        # Edges — only between nodes in this graph
        module_ids = {n.id for n in modules}
        for i, edge in enumerate(snapshot.edges):
            if edge.source in module_ids and edge.target in module_ids:
                cy_edges.append({
                    "data": {
                        "id": f"e{i}",
                        "source": edge.source,
                        "target": edge.target,
                        "weight": edge.weight,
                        "label": str(edge.weight) if edge.weight > 1 else "",
                    }
                })

        return jsonify({
            "nodes": cy_nodes,
            "edges": cy_edges,
            "capabilities": list(snapshot.capability_map.keys()),
            "module_count": len(modules),
            "edge_count": len(cy_edges),
        })

    # --- Utility routes ---

    @app.route("/api/snapshot")
    def snapshot_json():
        snapshot, _ = _get_snapshot()
        return jsonify(json.loads(snapshot.model_dump_json()))

    @app.route("/api/modules")
    def modules():
        snapshot, _ = _get_snapshot()
        return jsonify(
            [
                {
                    "id": n.id,
                    "name": n.name,
                    "loc": n.lines_of_code,
                    "capability": n.capability,
                    "summary": n.ai_summary,
                }
                for n in snapshot.get_module_nodes()
            ]
        )

    @app.route("/api/module-map")
    def module_map():
        """Return a mapping from Mermaid-sanitised node ID -> module_id."""
        snapshot, _ = _get_snapshot()
        mapping = {_sanitize_for_mermaid(n.id): n.id for n in snapshot.get_module_nodes()}
        return jsonify(mapping)

    @app.route("/api/refresh")
    def refresh():
        """Clear the analysis cache and re-analyse on next request."""
        _cache.clear()
        return jsonify({"status": "cache cleared"})

    return app


def _sanitize_for_mermaid(raw: str) -> str:
    return (
        raw.replace(":", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
        .replace(" ", "_")
        .replace("<", "")
        .replace(">", "")
    )


def _extract_mermaid(markdown: str) -> str:
    """Pull the Mermaid code block out of a generated markdown string."""
    in_block = False
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.strip() == "```mermaid":
            in_block = True
            continue
        if in_block and line.strip() == "```":
            break
        if in_block:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Embedded HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ArchiLens — {{ project_name }}</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0d1117;
      color: #e6edf3;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }

    /* ── Header ───────────────────────────────────────────────────── */
    header {
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 10px 20px;
      display: flex;
      align-items: center;
      gap: 16px;
      flex-shrink: 0;
    }
    header h1 { font-size: 16px; font-weight: 600; color: #58a6ff; }
    header .ref {
      font-size: 12px; color: #8b949e; font-family: monospace;
      background: #21262d; padding: 2px 8px; border-radius: 4px;
    }
    header .actions { margin-left: auto; display: flex; gap: 8px; }
    .btn {
      background: #21262d; border: 1px solid #30363d; color: #e6edf3;
      padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
      transition: background 0.15s; text-decoration: none; display: inline-block;
    }
    .btn:hover { background: #30363d; }
    .btn.primary { background: #1f6feb; border-color: #1f6feb; }
    .btn.primary:hover { background: #388bfd; }

    /* ── Layout ───────────────────────────────────────────────────── */
    .main { display: flex; flex: 1; overflow: hidden; }

    /* ── Sidebar ──────────────────────────────────────────────────── */
    #sidebar {
      width: 240px; background: #161b22;
      border-right: 1px solid #30363d;
      display: flex; flex-direction: column; flex-shrink: 0;
    }
    .sidebar-search { padding: 10px; border-bottom: 1px solid #30363d; }
    .sidebar-search input {
      width: 100%; background: #0d1117; border: 1px solid #30363d; color: #e6edf3;
      padding: 6px 10px; border-radius: 6px; font-size: 13px;
    }
    .sidebar-search input:focus { outline: none; border-color: #58a6ff; }
    #nav-list { list-style: none; overflow-y: auto; flex: 1; padding: 4px 0; }
    #nav-list li {
      padding: 6px 14px; cursor: pointer; font-size: 13px;
      border-left: 3px solid transparent; transition: background 0.1s;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    #nav-list li:hover { background: #21262d; }
    #nav-list li.active { background: #21262d; border-left-color: #58a6ff; color: #58a6ff; }
    #nav-list li[data-level="0"] { color: #ff7b72; font-weight: 600; }
    #nav-list li[data-level="1"] { color: #79c0ff; font-weight: 600; }
    #nav-list li[data-level="2"] { color: #d2a8ff; padding-left: 24px; }
    #nav-list li[data-level="3"] { color: #ffa657; padding-left: 24px; }

    /* ── Content ──────────────────────────────────────────────────── */
    #content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
    #diagram-title {
      padding: 10px 20px; border-bottom: 1px solid #30363d;
      font-size: 14px; font-weight: 600; color: #8b949e;
      background: #161b22; display: flex; align-items: center; gap: 12px;
      flex-shrink: 0;
    }
    #level-badge {
      font-size: 11px; font-weight: 700; padding: 2px 8px;
      border-radius: 12px; background: #21262d;
    }

    /* ── Zoom controls (Mermaid) ──────────────────────────────────── */
    #zoom-controls { display: none; align-items: center; gap: 4px; margin-left: auto; }
    .zoom-btn {
      background: #21262d; border: 1px solid #30363d; color: #e6edf3;
      padding: 3px 9px; border-radius: 5px; cursor: pointer; font-size: 14px;
      line-height: 1.4; transition: background 0.15s;
    }
    .zoom-btn:hover { background: #30363d; }
    #zoom-label {
      font-size: 12px; color: #8b949e; min-width: 38px;
      text-align: center; font-variant-numeric: tabular-nums;
    }

    /* ── Cytoscape controls (L1) ──────────────────────────────────── */
    #cy-controls {
      display: none; align-items: center; gap: 6px; margin-left: auto;
    }
    #cy-controls select {
      background: #21262d; border: 1px solid #30363d; color: #e6edf3;
      padding: 3px 8px; border-radius: 5px; font-size: 12px; cursor: pointer;
    }
    #cy-info { font-size: 12px; color: #8b949e; }

    /* ── Diagram area ─────────────────────────────────────────────── */
    #diagram-area { flex: 1; overflow: hidden; position: relative; }

    /* Mermaid viewport */
    #mermaid-viewport {
      display: none;
      width: 100%; height: 100%;
      overflow: auto; padding: 24px;
      cursor: grab; user-select: none; -webkit-user-select: none;
    }
    #mermaid-viewport.grabbing { cursor: grabbing; }
    #mermaid-viewport .mermaid {
      display: inline-block; transform-origin: top left;
      background: #161b22; border: 1px solid #30363d;
      border-radius: 8px; padding: 24px;
    }
    #mermaid-viewport svg { display: block; }

    /* Cytoscape viewport */
    #cy-viewport {
      display: none;
      width: 100%; height: 100%;
      background: #0d1117;
    }

    /* Tooltip */
    #cy-tooltip {
      display: none; position: fixed; z-index: 100;
      background: #161b22; border: 1px solid #30363d;
      border-radius: 6px; padding: 8px 12px;
      font-size: 12px; max-width: 260px; pointer-events: none;
      box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    }
    #cy-tooltip strong { display: block; color: #e6edf3; margin-bottom: 4px; }
    #cy-tooltip .cap { color: #58a6ff; font-size: 11px; }
    #cy-tooltip .summary { color: #8b949e; margin-top: 4px; }

    /* Empty / error state */
    #empty-state {
      color: #8b949e; text-align: center; margin-top: 80px;
      font-size: 14px; line-height: 1.8;
    }
    #empty-state h2 { margin-bottom: 8px; color: #e6edf3; font-size: 20px; }

    /* Loading overlay */
    #loading {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.6); z-index: 999;
      align-items: center; justify-content: center;
      flex-direction: column; gap: 12px;
      font-size: 14px; color: #e6edf3;
    }
    .spinner {
      width: 24px; height: 24px;
      border: 3px solid #30363d; border-top-color: #58a6ff;
      border-radius: 50%; animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

<header>
  <h1>ArchiLens</h1>
  <span>{{ project_name }}</span>
  <span class="ref">{{ git_ref }}</span>
  <div class="actions">
    <button class="btn" onclick="refreshAnalysis()">Re-analyse</button>
    <a href="/api/snapshot" target="_blank" class="btn">JSON</a>
  </div>
</header>

<div class="main">
  <nav id="sidebar">
    <div class="sidebar-search">
      <input type="search" id="search" placeholder="Filter..." oninput="filterNav(this.value)">
    </div>
    <ul id="nav-list"></ul>
  </nav>

  <div id="content">
    <div id="diagram-title">
      <span id="level-badge">—</span>
      <span id="title-text">Select a diagram from the sidebar</span>

      <!-- Mermaid zoom controls (L0 / L2 / L3) -->
      <div id="zoom-controls">
        <button class="zoom-btn" onclick="zoomOut()">−</button>
        <span id="zoom-label">100%</span>
        <button class="zoom-btn" onclick="zoomIn()">+</button>
        <button class="zoom-btn" onclick="fitDiagram()">Fit</button>
        <button class="zoom-btn" onclick="resetZoom()">1:1</button>
      </div>

      <!-- Cytoscape controls (L1) -->
      <div id="cy-controls">
        <span id="cy-info"></span>
        <button class="zoom-btn" onclick="cyFit()">Fit</button>
        <button class="zoom-btn" onclick="cyReLayout()">Re-layout</button>
        <select id="layout-select" onchange="cyReLayout()">
          <option value="cose">Force (CoSE)</option>
          <option value="breadthfirst">Hierarchical</option>
          <option value="grid">Grid</option>
          <option value="circle">Circle</option>
        </select>
      </div>
    </div>

    <div id="diagram-area">
      <div id="mermaid-viewport">
        <div id="empty-state">
          <h2>Welcome to ArchiLens</h2>
          <p>Select a diagram level from the sidebar.<br>
          Use <strong>Re-analyse</strong> to pick up code changes.</p>
        </div>
      </div>
      <div id="cy-viewport"></div>
    </div>
  </div>
</div>

<div id="loading">
  <div class="spinner"></div>
  <span id="loading-msg">Loading...</span>
</div>

<div id="cy-tooltip"></div>

<script>
// ── Mermaid config ────────────────────────────────────────────────
mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  themeVariables: {
    background: '#161b22',
    primaryColor: '#1f6feb',
    primaryTextColor: '#e6edf3',
    edgeLabelBackground: '#21262d',
    lineColor: '#8b949e',
  },
  flowchart: { htmlLabels: true, curve: 'basis' },
  sequence: { diagramMarginX: 30, diagramMarginY: 10 },
});

// ── App state ─────────────────────────────────────────────────────
let navItems = [];
let currentId = null;
let _cy = null;  // Cytoscape instance

// ── Boot ──────────────────────────────────────────────────────────
async function boot() {
  showLoading('Analysing repository...');
  try {
    const res = await fetch('/api/nav');
    navItems = await res.json();
    renderNav(navItems);
    const l1 = navItems.find(i => i.id === 'l1');
    if (l1) selectItem(l1);
  } catch (e) {
    showMermaidPanel();
    document.getElementById('mermaid-viewport').innerHTML =
      '<div id="empty-state"><h2>Analysis failed</h2><p>' + e.message + '</p></div>';
  } finally {
    hideLoading();
  }
}

// ── Navigation ────────────────────────────────────────────────────
function renderNav(items) {
  const ul = document.getElementById('nav-list');
  ul.innerHTML = '';
  items.forEach(item => {
    const li = document.createElement('li');
    li.textContent = item.label;
    li.dataset.id   = item.id;
    li.dataset.level = item.level;
    li.onclick = () => selectItem(item);
    ul.appendChild(li);
  });
}

function filterNav(query) {
  const q = query.toLowerCase();
  renderNav(q ? navItems.filter(i => i.label.toLowerCase().includes(q)) : navItems);
}

async function selectItem(item) {
  if (currentId === item.id) return;
  currentId = item.id;
  document.querySelectorAll('#nav-list li').forEach(li => {
    li.classList.toggle('active', li.dataset.id === item.id);
  });

  showLoading('Rendering diagram...');
  try {
    if (item.id === 'l1') {
      await loadCytoscapeL1();
    } else {
      const data = await fetchMermaidDiagram(item);
      renderMermaid(data);
    }
  } catch(e) {
    showMermaidPanel();
    document.getElementById('mermaid-viewport').innerHTML =
      '<div id="empty-state"><h2>Error</h2><p>' + e.message + '</p></div>';
  } finally {
    hideLoading();
  }
}

async function fetchMermaidDiagram(item) {
  let url;
  if (item.id === 'l0') url = '/api/diagram/l0';
  else if (item.level === 2) url = '/api/diagram/l2/' + encodeURIComponent(item.module_id);
  else if (item.level === 3) url = '/api/diagram/l3/' + encodeURIComponent(item.flow_id);
  else throw new Error('Unknown diagram type');
  const res = await fetch(url);
  if (!res.ok) throw new Error('Server returned ' + res.status);
  return res.json();
}

// ── Cytoscape L1 ─────────────────────────────────────────────────
const CAP_COLORS = [
  '#1f6feb','#2ea043','#9e6a03','#8957e5',
  '#da3633','#0075ca','#bf8700','#5a6e16'
];

async function loadCytoscapeL1() {
  const res  = await fetch('/api/graph/l1');
  if (!res.ok) throw new Error('Failed to load graph data');
  const data = await res.json();

  // Update title bar
  const levelColors = { 1: '#79c0ff' };
  document.getElementById('title-text').textContent = 'L1: Module Architecture';
  const badge = document.getElementById('level-badge');
  badge.textContent = 'L1';
  badge.style.color = '#79c0ff';

  document.getElementById('cy-info').textContent =
    data.module_count + ' modules · ' + data.edge_count + ' edges';

  showCyPanel();

  // Build capability color map
  const capColorMap = {};
  (data.capabilities || []).forEach((cap, i) => {
    capColorMap[cap] = CAP_COLORS[i % CAP_COLORS.length];
  });

  // Destroy previous instance
  if (_cy) { _cy.destroy(); _cy = null; }

  const container = document.getElementById('cy-viewport');
  const elements  = [...data.nodes, ...data.edges];

  _cy = cytoscape({
    container,
    elements,
    style: [
      // Capability compound parent nodes
      {
        selector: '.capability-group',
        style: {
          'label':             'data(label)',
          'background-color':  '#161b22',
          'background-opacity': 0.5,
          'border-color':      'data(color)',
          'border-width':       2,
          'color':             '#c9d1d9',
          'font-size':         13,
          'font-weight':       600,
          'text-valign':       'top',
          'text-halign':       'center',
          'text-margin-y':     -6,
          'shape':             'round-rectangle',
          'padding':           '18px',
        }
      },
      // Module nodes
      {
        selector: '.module-node',
        style: {
          'label':             'data(label)',
          'background-color':  (ele) => {
            const cap = ele.data('capability');
            return cap && capColorMap[cap] ? capColorMap[cap] : '#21262d';
          },
          'background-opacity': 0.85,
          'border-color':       '#30363d',
          'border-width':        1,
          'color':              '#e6edf3',
          'font-size':           11,
          'text-valign':         'center',
          'text-halign':         'center',
          'shape':               'round-rectangle',
          'width':               (ele) => {
            const loc = ele.data('loc') || 0;
            return Math.min(180, Math.max(60, 60 + loc / 60));
          },
          'height':              32,
          'cursor':              'pointer',
          'text-wrap':           'wrap',
          'text-max-width':       160,
        }
      },
      // Edges
      {
        selector: 'edge',
        style: {
          'width':                  (ele) => Math.min(5, Math.max(1, ele.data('weight') / 5)),
          'line-color':             '#30363d',
          'target-arrow-color':     '#8b949e',
          'target-arrow-shape':     'triangle',
          'curve-style':            'bezier',
          'label':                  'data(label)',
          'font-size':               9,
          'color':                  '#8b949e',
          'text-background-color':  '#0d1117',
          'text-background-opacity': 0.85,
          'text-background-padding': '2px',
          'opacity':                 0.7,
        }
      },
      // Hover highlight
      {
        selector: '.module-node:active, .module-node.highlighted',
        style: {
          'border-color':   '#58a6ff',
          'border-width':    2,
          'background-opacity': 1,
        }
      },
    ],
    layout: _cyLayout('cose'),
    wheelSensitivity: 0.2,
    minZoom: 0.05,
    maxZoom: 4,
  });

  // Click to drill into L2
  _cy.on('tap', '.module-node', (evt) => {
    const moduleId = evt.target.id();
    const l2Item = navItems.find(i => i.level === 2 && i.module_id === moduleId);
    if (l2Item) selectItem(l2Item);
  });

  // Tooltip on hover
  const tooltip = document.getElementById('cy-tooltip');
  _cy.on('mouseover', '.module-node', (evt) => {
    const d = evt.target.data();
    tooltip.innerHTML =
      '<strong>' + d.label + '</strong>' +
      (d.capability ? '<div class="cap">' + d.capability + '</div>' : '') +
      '<div>' + (d.loc ? d.loc.toLocaleString() + ' LOC' : '') + '</div>' +
      (d.summary ? '<div class="summary">' + d.summary.slice(0, 120) + '</div>' : '');
    tooltip.style.display = 'block';
  });
  _cy.on('mousemove', (evt) => {
    if (tooltip.style.display !== 'none') {
      tooltip.style.left = (evt.originalEvent.clientX + 14) + 'px';
      tooltip.style.top  = (evt.originalEvent.clientY + 14) + 'px';
    }
  });
  _cy.on('mouseout', '.module-node', () => { tooltip.style.display = 'none'; });
}

function _cyLayout(name) {
  if (name === 'cose') {
    return {
      name:            'cose',
      animate:          false,
      nodeRepulsion:    450000,
      idealEdgeLength:  100,
      edgeElasticity:   100,
      nestingFactor:      5,
      gravity:           80,
      numIter:         1000,
      initialTemp:      200,
      coolingFactor:    0.95,
    };
  }
  return { name, animate: false, padding: 30 };
}

function cyFit()      { if (_cy) _cy.fit(undefined, 40); }
function cyReLayout() {
  if (!_cy) return;
  const name = document.getElementById('layout-select').value;
  _cy.layout(_cyLayout(name)).run();
}

// ── Mermaid rendering ─────────────────────────────────────────────
let _zoom = 1.0;
const ZOOM_STEP = 0.15, MIN_ZOOM = 0.1, MAX_ZOOM = 5.0;

function _applyZoom(z) {
  _zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  const el = document.querySelector('#mermaid-viewport .mermaid');
  if (el) el.style.transform = 'scale(' + _zoom + ')';
  document.getElementById('zoom-label').textContent = Math.round(_zoom * 100) + '%';
}
function zoomIn()    { _applyZoom(_zoom + ZOOM_STEP); }
function zoomOut()   { _applyZoom(_zoom - ZOOM_STEP); }
function resetZoom() { _applyZoom(1.0); }

function fitDiagram() {
  const vp  = document.getElementById('mermaid-viewport');
  const svg = vp.querySelector('svg');
  if (!svg) return;
  const naturalW = parseFloat(svg.style.width)  || svg.getBoundingClientRect().width  / _zoom;
  const naturalH = parseFloat(svg.style.height) || svg.getBoundingClientRect().height / _zoom;
  if (!naturalW || !naturalH) return;
  const pad = 48;
  const scaleX = (vp.clientWidth  - pad) / naturalW;
  const scaleY = (vp.clientHeight - pad) / naturalH;
  _applyZoom(Math.min(scaleX, scaleY));
  vp.scrollTop = 0; vp.scrollLeft = 0;
}

// Wheel zoom for Mermaid viewport
document.getElementById('diagram-area').addEventListener('wheel', e => {
  if (document.getElementById('mermaid-viewport').style.display === 'none') return;
  if (!document.querySelector('#mermaid-viewport .mermaid')) return;
  e.preventDefault();
  _applyZoom(_zoom * (e.deltaY < 0 ? 1.12 : 0.89));
}, { passive: false });

// Pan for Mermaid viewport
(function () {
  const vp = document.getElementById('mermaid-viewport');
  let dragging = false, startX, startY, scrollX, scrollY;
  vp.addEventListener('mousedown', e => {
    if (e.target.closest('a, button, [data-level]')) return;
    dragging = true;
    startX = e.clientX; startY = e.clientY;
    scrollX = vp.scrollLeft; scrollY = vp.scrollTop;
    vp.classList.add('grabbing');
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    vp.scrollLeft = scrollX - (e.clientX - startX);
    vp.scrollTop  = scrollY - (e.clientY - startY);
  });
  window.addEventListener('mouseup', () => { dragging = false; vp.classList.remove('grabbing'); });
})();

async function renderMermaid(data) {
  const vp = document.getElementById('mermaid-viewport');
  const levelColors = { 0: '#ff7b72', 2: '#d2a8ff', 3: '#ffa657' };

  document.getElementById('title-text').textContent = data.title || '—';
  const badge = document.getElementById('level-badge');
  badge.textContent = 'L' + data.level;
  badge.style.color = levelColors[data.level] || '#8b949e';

  showMermaidPanel();

  if (!data.mermaid || data.empty) {
    vp.innerHTML = '<div id="empty-state"><h2>No components found</h2><p>This module has no detected classes or components.</p></div>';
    document.getElementById('zoom-controls').style.display = 'none';
    return;
  }

  const el = document.createElement('div');
  el.className = 'mermaid';
  el.textContent = data.mermaid;
  vp.innerHTML = '';
  vp.appendChild(el);

  try {
    await mermaid.run({ nodes: [el] });
    const svg = el.querySelector('svg');
    if (svg) {
      const vb = svg.viewBox && svg.viewBox.baseVal;
      if (vb && vb.width) {
        svg.style.width  = vb.width  + 'px';
        svg.style.height = vb.height + 'px';
      }
      svg.removeAttribute('width'); svg.removeAttribute('height');
      svg.style.maxWidth = 'none'; svg.style.display = 'block';
    }
    document.getElementById('zoom-controls').style.display = 'flex';
    _zoom = 1.0;
    setTimeout(fitDiagram, 50);
  } catch(e) {
    vp.innerHTML = '<div id="empty-state"><h2>Render error</h2><p>' + e.message +
      '</p><pre style="text-align:left;font-size:11px;margin-top:12px;color:#8b949e">' +
      data.mermaid + '</pre></div>';
  }
}

// ── Panel switching ───────────────────────────────────────────────
function showMermaidPanel() {
  document.getElementById('mermaid-viewport').style.display = 'block';
  document.getElementById('cy-viewport').style.display     = 'none';
  document.getElementById('zoom-controls').style.display   = 'flex';
  document.getElementById('cy-controls').style.display     = 'none';
  document.getElementById('cy-tooltip').style.display      = 'none';
}
function showCyPanel() {
  document.getElementById('mermaid-viewport').style.display = 'none';
  document.getElementById('cy-viewport').style.display      = 'block';
  document.getElementById('zoom-controls').style.display    = 'none';
  document.getElementById('cy-controls').style.display      = 'flex';
}

// ── Refresh ───────────────────────────────────────────────────────
async function refreshAnalysis() {
  showLoading('Re-analysing repository...');
  currentId = null;
  if (_cy) { _cy.destroy(); _cy = null; }
  await fetch('/api/refresh');
  await boot();
}

// ── Helpers ───────────────────────────────────────────────────────
function showLoading(msg) {
  document.getElementById('loading').style.display = 'flex';
  document.getElementById('loading-msg').textContent = msg || 'Loading...';
}
function hideLoading() {
  document.getElementById('loading').style.display = 'none';
}

boot();
</script>
</body>
</html>
"""

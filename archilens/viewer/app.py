"""
Flask-based interactive architecture viewer.

Runs the full ArchiLens analysis pipeline on demand and serves an
interactive single-page UI with:
  - Sidebar navigation tree (L0 → L1 → L2 per module → L3 per flow)
  - Mermaid.js diagram rendering in the main panel
  - Drill-down by clicking module nodes in L1 diagrams
  - Search / filter for large repos

Usage:
    archilens serve --repo /path/to/repo --port 8765
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def create_app(repo_path: str | Path, use_ai: bool = False) -> "Flask":  # type: ignore[name-defined]
    """
    Create and configure the Flask application.

    Args:
        repo_path:  Repository root to analyse.
        use_ai:     Whether to enable AI-powered diagram enrichment.
    """
    try:
        from flask import Flask, jsonify, render_template_string, request  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "Flask is required for the viewer. "
            "Install with: pip install archilens[viewer]"
        )

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
            items.append({
                "id": f"l2_{safe}",
                "label": f"  {mod.name}",
                "level": 2,
                "module_id": mod.id,
            })

        for flow in snapshot.flows:
            safe = flow.name.lower().replace(" ", "_").replace("/", "_")
            items.append({
                "id": f"l3_{safe}",
                "label": f"  ⚡ {flow.name}",
                "level": 3,
                "flow_id": flow.id,
            })

        return jsonify(items)

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
        # module_id may be URL-encoded; find by name match
        node = next(
            (n for n in snapshot.get_module_nodes()
             if n.id == module_id or n.name.lower().replace(" ", "_") == module_id),
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
            (f for f in snapshot.flows
             if f.id == flow_id or f.name.lower().replace(" ", "_").replace("/", "_") == flow_id),
            None,
        )
        if flow is None:
            return jsonify({"error": "Flow not found"}), 404

        content = generate_process_flow(flow)
        mermaid_code = _extract_mermaid(content)
        return jsonify({"mermaid": mermaid_code, "title": f"L3: {flow.name}", "level": 3})

    @app.route("/api/snapshot")
    def snapshot_json():
        snapshot, _ = _get_snapshot()
        return jsonify(json.loads(snapshot.model_dump_json()))

    @app.route("/api/modules")
    def modules():
        snapshot, _ = _get_snapshot()
        return jsonify([
            {
                "id": n.id,
                "name": n.name,
                "loc": n.lines_of_code,
                "capability": n.capability,
                "summary": n.ai_summary,
            }
            for n in snapshot.get_module_nodes()
        ])

    @app.route("/api/module-map")
    def module_map():
        """
        Return a mapping from Mermaid-sanitised node ID -> module_id.

        The L1 flowchart uses sanitised IDs (colons, slashes → underscores).
        The browser uses this map to wire click-to-drill-down on SVG nodes.
        """
        snapshot, _ = _get_snapshot()
        mapping = {
            _sanitize_for_mermaid(n.id): n.id
            for n in snapshot.get_module_nodes()
        }
        return jsonify(mapping)

    @app.route("/api/refresh")
    def refresh():
        """Clear the analysis cache and re-analyse on next request."""
        _cache.clear()
        return jsonify({"status": "cache cleared"})

    return app


def _sanitize_for_mermaid(raw: str) -> str:
    """Mirror the sanitisation used by the Mermaid generator."""
    return (
        raw.replace(":", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
        .replace(" ", "_")
        .replace("<", "")
        .replace(">", "")
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

    /* ── Header ─────────────────────────────────────────────────── */
    header {
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 10px 20px;
      display: flex;
      align-items: center;
      gap: 16px;
      flex-shrink: 0;
    }
    header h1 {
      font-size: 16px;
      font-weight: 600;
      color: #58a6ff;
    }
    header .ref {
      font-size: 12px;
      color: #8b949e;
      font-family: monospace;
      background: #21262d;
      padding: 2px 8px;
      border-radius: 4px;
    }
    header .actions {
      margin-left: auto;
      display: flex;
      gap: 8px;
    }
    .btn {
      background: #21262d;
      border: 1px solid #30363d;
      color: #e6edf3;
      padding: 5px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      transition: background 0.15s;
    }
    .btn:hover { background: #30363d; }
    .btn.primary { background: #1f6feb; border-color: #1f6feb; }
    .btn.primary:hover { background: #388bfd; }

    /* ── Main layout ─────────────────────────────────────────────── */
    .main {
      display: flex;
      flex: 1;
      overflow: hidden;
    }

    /* ── Sidebar ─────────────────────────────────────────────────── */
    #sidebar {
      width: 240px;
      background: #161b22;
      border-right: 1px solid #30363d;
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }
    .sidebar-search {
      padding: 10px;
      border-bottom: 1px solid #30363d;
    }
    .sidebar-search input {
      width: 100%;
      background: #0d1117;
      border: 1px solid #30363d;
      color: #e6edf3;
      padding: 6px 10px;
      border-radius: 6px;
      font-size: 13px;
    }
    .sidebar-search input:focus { outline: none; border-color: #58a6ff; }

    #nav-list {
      list-style: none;
      overflow-y: auto;
      flex: 1;
      padding: 4px 0;
    }
    #nav-list li {
      padding: 6px 14px;
      cursor: pointer;
      font-size: 13px;
      border-left: 3px solid transparent;
      transition: background 0.1s;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    #nav-list li:hover { background: #21262d; }
    #nav-list li.active {
      background: #21262d;
      border-left-color: #58a6ff;
      color: #58a6ff;
    }
    #nav-list li[data-level="0"] { color: #ff7b72; font-weight: 600; }
    #nav-list li[data-level="1"] { color: #79c0ff; font-weight: 600; }
    #nav-list li[data-level="2"] { color: #d2a8ff; padding-left: 24px; }
    #nav-list li[data-level="3"] { color: #ffa657; padding-left: 24px; }

    /* ── Content panel ───────────────────────────────────────────── */
    #content {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    #diagram-title {
      padding: 10px 20px;
      border-bottom: 1px solid #30363d;
      font-size: 14px;
      font-weight: 600;
      color: #8b949e;
      background: #161b22;
      display: flex;
      align-items: center;
      gap: 12px;
    }
    #level-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 12px;
      background: #21262d;
    }

    #diagram-area {
      flex: 1;
      overflow: auto;
      padding: 24px;
      display: flex;
      flex-direction: column;
      align-items: stretch;
    }
    #diagram-area .mermaid {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 24px;
      width: 100%;
      flex: 1;
    }
    #diagram-area svg {
      width: 100% !important;
      max-width: 100% !important;
      height: auto !important;
      min-height: 420px;
      display: block;
    }
    #empty-state {
      color: #8b949e;
      text-align: center;
      margin-top: 80px;
      font-size: 14px;
      line-height: 1.8;
    }
    #empty-state h2 { margin-bottom: 8px; color: #e6edf3; font-size: 20px; }

    /* ── Loading spinner ─────────────────────────────────────────── */
    .spinner {
      display: none;
      width: 24px;
      height: 24px;
      border: 3px solid #30363d;
      border-top-color: #58a6ff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin: 0 auto;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    #loading {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 999;
      align-items: center;
      justify-content: center;
      flex-direction: column;
      gap: 12px;
      font-size: 14px;
      color: #e6edf3;
    }
  </style>
</head>
<body>

<header>
  <h1>⬡ ArchiLens</h1>
  <span>{{ project_name }}</span>
  <span class="ref">{{ git_ref }}</span>
  <div class="actions">
    <button class="btn" onclick="refreshAnalysis()">↻ Re-analyse</button>
    <a href="/api/snapshot" target="_blank" class="btn">{ } JSON</a>
  </div>
</header>

<div class="main">
  <nav id="sidebar">
    <div class="sidebar-search">
      <input type="search" id="search" placeholder="Filter…" oninput="filterNav(this.value)">
    </div>
    <ul id="nav-list"></ul>
  </nav>

  <div id="content">
    <div id="diagram-title">
      <span id="level-badge">—</span>
      <span id="title-text">Select a diagram from the sidebar</span>
    </div>
    <div id="diagram-area">
      <div id="empty-state">
        <h2>Welcome to ArchiLens</h2>
        <p>Select a diagram level from the sidebar on the left.<br>
        The viewer runs the full analysis pipeline in-process.<br>
        Use <strong>↻ Re-analyse</strong> to pick up code changes.</p>
      </div>
    </div>
  </div>
</div>

<div id="loading">
  <div class="spinner" style="display:block"></div>
  <span id="loading-msg">Loading…</span>
</div>

<script>
  // Mermaid config — dark GitHub theme
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

  let navItems = [];
  let currentId = null;

  // ── Boot ────────────────────────────────────────────────────────
  async function boot() {
    showLoading('Analysing repository…');
    try {
      const res = await fetch('/api/nav');
      navItems = await res.json();
      renderNav(navItems);
      // Auto-select L1 on first load
      const l1 = navItems.find(i => i.id === 'l1');
      if (l1) selectItem(l1);
    } catch (e) {
      document.getElementById('empty-state').innerHTML =
        '<h2>Analysis failed</h2><p>' + e.message + '</p>';
    } finally {
      hideLoading();
    }
  }

  // ── Navigation ──────────────────────────────────────────────────
  function renderNav(items) {
    const ul = document.getElementById('nav-list');
    ul.innerHTML = '';
    items.forEach(item => {
      const li = document.createElement('li');
      li.textContent = item.label;
      li.dataset.id = item.id;
      li.dataset.level = item.level;
      li.onclick = () => selectItem(item);
      ul.appendChild(li);
    });
  }

  function filterNav(query) {
    const q = query.toLowerCase();
    const filtered = q ? navItems.filter(i => i.label.toLowerCase().includes(q)) : navItems;
    renderNav(filtered);
  }

  async function selectItem(item) {
    if (currentId === item.id) return;
    currentId = item.id;

    // Update active state
    document.querySelectorAll('#nav-list li').forEach(li => {
      li.classList.toggle('active', li.dataset.id === item.id);
    });

    showLoading('Rendering diagram…');
    try {
      const data = await fetchDiagram(item);
      renderDiagram(data);
    } catch (e) {
      showError(e.message);
    } finally {
      hideLoading();
    }
  }

  async function fetchDiagram(item) {
    let url;
    if (item.id === 'l0') url = '/api/diagram/l0';
    else if (item.id === 'l1') url = '/api/diagram/l1';
    else if (item.level === 2) url = `/api/diagram/l2/${encodeURIComponent(item.module_id)}`;
    else if (item.level === 3) url = `/api/diagram/l3/${encodeURIComponent(item.flow_id)}`;
    else throw new Error('Unknown diagram type');

    const res = await fetch(url);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    return await res.json();
  }

  // ── Diagram rendering ────────────────────────────────────────────
  async function renderDiagram(data) {
    const area = document.getElementById('diagram-area');
    const levelColors = { 0: '#ff7b72', 1: '#79c0ff', 2: '#d2a8ff', 3: '#ffa657' };

    document.getElementById('title-text').textContent = data.title || '—';
    const badge = document.getElementById('level-badge');
    badge.textContent = `L${data.level}`;
    badge.style.color = levelColors[data.level] || '#8b949e';

    if (!data.mermaid || data.empty) {
      area.innerHTML = '<div id="empty-state"><h2>No components found</h2><p>This module has no detected classes or components.</p></div>';
      return;
    }

    // Use textContent (not innerHTML) so <<annotations>> aren't
    // mangled by the HTML parser before Mermaid reads them.
    const uid = 'mermaid_' + Date.now();
    const el = document.createElement('div');
    el.className = 'mermaid';
    el.id = uid;
    el.textContent = data.mermaid;
    area.innerHTML = '';
    area.appendChild(el);

    try {
      await mermaid.run({ nodes: [el] });

      // Wire click-to-drill-down for L1 module nodes
      if (data.level === 1) {
        let modMap = {};
        try {
          const r = await fetch('/api/module-map');
          modMap = await r.json();
        } catch (_) {}

        // Mermaid renders flowchart nodes as SVG elements with IDs like
        // "flowchart-{nodeId}-{index}". We strip the prefix and suffix to
        // recover the sanitised node ID, then look it up in modMap.
        document.querySelectorAll('[id^="flowchart-"]').forEach(el => {
          const mermaidId = el.id.replace(/^flowchart-/, '').replace(/-\d+$/, '');
          const moduleId = modMap[mermaidId];
          if (!moduleId) return;
          const l2Item = navItems.find(i => i.level === 2 && i.module_id === moduleId);
          if (!l2Item) return;
          el.style.cursor = 'pointer';
          el.title = `Drill into ${l2Item.label.trim()}`;
          el.addEventListener('click', (e) => {
            e.stopPropagation();
            selectItem(l2Item);
          });
        });
      }
    } catch (e) {
      area.innerHTML = `<div id="empty-state">
        <h2>Render error</h2>
        <p>${e.message}</p>
        <pre style="text-align:left;font-size:12px;margin-top:12px;color:#8b949e">${data.mermaid}</pre>
      </div>`;
    }
  }

  // ── Refresh ──────────────────────────────────────────────────────
  async function refreshAnalysis() {
    showLoading('Re-analysing repository…');
    currentId = null;
    await fetch('/api/refresh');
    await boot();
  }

  // ── Helpers ──────────────────────────────────────────────────────
  function showLoading(msg) {
    const el = document.getElementById('loading');
    el.style.display = 'flex';
    document.getElementById('loading-msg').textContent = msg || 'Loading…';
  }
  function hideLoading() {
    document.getElementById('loading').style.display = 'none';
  }
  function showError(msg) {
    document.getElementById('diagram-area').innerHTML =
      `<div id="empty-state"><h2>Error</h2><p>${msg}</p></div>`;
  }

  boot();
</script>
</body>
</html>
"""

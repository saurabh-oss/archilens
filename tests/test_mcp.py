"""Tests for the MCP server (archilens/mcp/server.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.fastmcp", reason="requires the 'mcp' extra (mcp 1.x)")

from archilens.mcp import cache, server  # noqa: E402

_CONFIG = """\
project:
  name: "Shop"
capabilities:
  - name: "Order Management"
    modules:
      - "src/orders/**"
ci:
  rules:
    - name: "no-layer-skip"
      from: "src/presentation/**"
      to: "src/infrastructure/**"
      action: "fail"
"""


@pytest.fixture
def mcp_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An MCP server bound to a small repo, with state and cache isolated per test."""
    repo = tmp_path / "repo"
    files = {
        "src/orders/service.py": "from src.infrastructure.db import conn\n\nclass OrderService:\n    pass\n",
        "src/presentation/views.py": "from src.orders.service import OrderService\n",
        "src/infrastructure/db.py": "conn = None\n",
        "src/app.py": "from src.presentation.views import OrderService\n",  # makes "src" a module too
    }
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (repo / ".archilens.yml").write_text(_CONFIG, encoding="utf-8")

    monkeypatch.setattr(cache, "_CACHE_DB", tmp_path / "cache" / "snapshots.db")
    monkeypatch.setattr(server, "_snapshot", None)
    monkeypatch.setattr(server, "_config", None)
    monkeypatch.setattr(server, "_repo_path", None)

    server.init_server(repo)
    return server.create_mcp_server()


def _call(mcp, name: str, args: dict) -> str:
    """Invoke a tool and return the text of its first content block."""
    result = asyncio.run(mcp.call_tool(name, args))
    content = result[0] if isinstance(result, tuple) else result
    return content[0].text


class TestServerFactory:
    def test_host_and_port_are_applied(self):
        mcp = server.create_mcp_server(host="0.0.0.0", port=9999)
        assert mcp.settings.host == "0.0.0.0"
        assert mcp.settings.port == 9999

    def test_defaults_to_loopback(self):
        assert server.create_mcp_server().settings.host == "127.0.0.1"

    def test_registers_all_tools(self, mcp_server):
        names = {t.name for t in asyncio.run(mcp_server.list_tools())}
        assert names == {
            "get_module_context",
            "check_dependency_allowed",
            "find_capability_owner",
            "get_architecture_context",
        }

    def test_tools_fail_clearly_before_init(self, monkeypatch):
        monkeypatch.setattr(server, "_snapshot", None)
        monkeypatch.setattr(server, "_repo_path", None)
        with pytest.raises(RuntimeError, match="not initialised"):
            server._ensure_loaded()


class TestTools:
    def test_get_module_context(self, mcp_server):
        ctx = json.loads(_call(mcp_server, "get_module_context", {"file_path": "src/orders/service.py"}))
        assert ctx["module_id"] == "module:src/orders"
        assert ctx["capability"] == "Order Management"
        assert "src/infrastructure" in ctx["top_dependencies"]
        assert "src/presentation" in ctx["depended_on_by"]

    def test_get_module_context_prefers_most_specific_module(self, mcp_server):
        # "src" and "src/orders" are both modules; the nested file belongs to the deeper one.
        nested = json.loads(_call(mcp_server, "get_module_context", {"file_path": "src/orders/service.py"}))
        top = json.loads(_call(mcp_server, "get_module_context", {"file_path": "src/app.py"}))
        assert nested["module_id"] == "module:src/orders"
        assert top["module_id"] == "module:src"

    def test_get_module_context_unknown_path(self, mcp_server):
        ctx = json.loads(_call(mcp_server, "get_module_context", {"file_path": "nowhere/x.py"}))
        assert "error" in ctx
        assert "Orders" in ctx["known_modules"]

    def test_check_dependency_blocked_by_rule(self, mcp_server):
        verdict = json.loads(
            _call(
                mcp_server,
                "check_dependency_allowed",
                {"source_file": "src/presentation/views.py", "target_module": "src/infrastructure"},
            )
        )
        assert verdict["allowed"] is False
        assert verdict["rule"] == "no-layer-skip"
        assert verdict["action"] == "fail"

    def test_check_dependency_allowed(self, mcp_server):
        verdict = json.loads(
            _call(
                mcp_server,
                "check_dependency_allowed",
                {"source_file": "src/presentation/views.py", "target_module": "src/orders"},
            )
        )
        assert verdict["allowed"] is True
        assert verdict["rule"] is None

    def test_find_capability_owner(self, mcp_server):
        owner = json.loads(_call(mcp_server, "find_capability_owner", {"feature_description": "order management"}))
        assert owner["match"]["module_id"] == "module:src/orders"

    def test_architecture_context_l1(self, mcp_server):
        text = _call(mcp_server, "get_architecture_context", {"scope": "L1"})
        assert "Shop" in text
        assert "Order Management: Orders" in text
        assert "src/presentation -> src/orders" in text

    def test_snapshot_is_cached_on_disk(self, mcp_server, tmp_path):
        _call(mcp_server, "get_architecture_context", {"scope": "L1"})
        assert (tmp_path / "cache" / "snapshots.db").exists()

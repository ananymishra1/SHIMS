from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.mcp_registry import (
    MCPClient,
    MCPServer,
    call_tool,
    get_server,
    list_servers,
    load_servers_config,
)


SAMPLE_SERVERS = {
    "servers": {
        "local_math": {
            "url": "http://127.0.0.1:9001",
            "endpoint": "/messages/",
            "timeout": 10,
        },
        "secure_docs": {
            "url": "http://127.0.0.1:9002/sse",
            "endpoint": "/",
            "headers": {"Authorization": "Bearer secret-token"},
            "timeout": 60,
        },
    }
}


def _make_mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = json_data if json_data is not None else {}
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.side_effect = None if status_code < 400 else Exception(f"HTTP {status_code}")
    return response


def _patched_httpx(responses: list[MagicMock]) -> "unittest.mock._patch":
    """Return a patch for httpx.Client that yields the given responses in order."""
    client_instance = MagicMock()
    client_instance.__enter__.return_value = client_instance
    client_instance.__exit__.return_value = False
    client_instance.post.side_effect = responses
    return patch("shared.mcp_registry.httpx.Client", return_value=client_instance)


class TestMCPServerConfig:
    def test_load_servers_config_reads_file(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "mcp_servers.json"
        cfg_path.write_text(json.dumps(SAMPLE_SERVERS))
        cfg = load_servers_config(cfg_path)
        assert "servers" in cfg
        assert cfg["servers"]["local_math"]["url"] == "http://127.0.0.1:9001"

    def test_load_servers_config_missing_file_returns_empty(self, tmp_path: Path) -> None:
        cfg = load_servers_config(tmp_path / "does_not_exist.json")
        assert cfg == {"servers": {}}

    def test_load_servers_config_bad_json_returns_error(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text("not-json")
        cfg = load_servers_config(cfg_path)
        assert "error" in cfg
        assert cfg["servers"] == {}

    def test_get_server_found(self) -> None:
        server = get_server("local_math", SAMPLE_SERVERS)
        assert server is not None
        assert server.name == "local_math"
        assert server.post_url() == "http://127.0.0.1:9001/messages/"

    def test_get_server_with_headers(self) -> None:
        server = get_server("secure_docs", SAMPLE_SERVERS)
        assert server is not None
        assert server.headers == {"Authorization": "Bearer secret-token"}
        assert server.post_url() == "http://127.0.0.1:9002/sse/"

    def test_get_server_missing(self) -> None:
        assert get_server("missing", SAMPLE_SERVERS) is None

    def test_list_servers(self) -> None:
        servers = list_servers(SAMPLE_SERVERS)
        assert len(servers) == 2
        names = {s["name"] for s in servers}
        assert names == {"local_math", "secure_docs"}


class TestMCPClient:
    def test_list_tools_success(self) -> None:
        server = MCPServer(name="test", url="http://127.0.0.1:9000")
        client = MCPClient(server)
        response = _make_mock_response(200, {"jsonrpc": "2.0", "id": "1", "result": {"tools": [{"name": "add"}]}})
        with _patched_httpx([response]) as mock_class:
            result = client.list_tools()
        assert result["ok"] is True
        assert result["result"]["tools"][0]["name"] == "add"
        call_kwargs = mock_class.return_value.post.call_args.kwargs
        assert call_kwargs["headers"] == {}

    def test_call_tool_success(self) -> None:
        server = MCPServer(name="test", url="http://127.0.0.1:9000")
        client = MCPClient(server)
        response = _make_mock_response(200, {"jsonrpc": "2.0", "id": "1", "result": {"content": [{"type": "text", "text": "42"}]}})
        with _patched_httpx([response]):
            result = client.call_tool("add", {"a": 20, "b": 22})
        assert result["ok"] is True
        assert result["result"]["content"][0]["text"] == "42"

    def test_call_tool_jsonrpc_error(self) -> None:
        server = MCPServer(name="test", url="http://127.0.0.1:9000")
        client = MCPClient(server)
        response = _make_mock_response(200, {"jsonrpc": "2.0", "id": "1", "error": {"code": -32601, "message": "Method not found"}})
        with _patched_httpx([response]):
            result = client.call_tool("unknown", {})
        assert result["ok"] is False
        assert "Method not found" in result["error"]
        assert result["code"] == -32601

    def test_call_tool_http_error(self) -> None:
        server = MCPServer(name="test", url="http://127.0.0.1:9000")
        client = MCPClient(server)
        response = _make_mock_response(500, text="Internal Server Error")
        response.raise_for_status.side_effect = Exception("HTTP 500")
        with _patched_httpx([response]):
            result = client.call_tool("add", {"a": 1, "b": 2})
        assert result["ok"] is False
        assert "500" in result["error"]

    def test_call_tool_network_error(self) -> None:
        server = MCPServer(name="test", url="http://127.0.0.1:9000")
        client = MCPClient(server)
        with patch("shared.mcp_registry.httpx.Client") as mock_class:
            mock_class.side_effect = Exception("connection refused")
            result = client.call_tool("add", {"a": 1, "b": 2})
        assert result["ok"] is False
        assert "connection refused" in result["error"]

    def test_request_includes_auth_header(self) -> None:
        server = MCPServer(name="secure", url="http://127.0.0.1:9002/sse", headers={"Authorization": "Bearer abc"})
        client = MCPClient(server)
        response = _make_mock_response(200, {"jsonrpc": "2.0", "id": "1", "result": {}})
        with _patched_httpx([response]) as mock_class:
            client.list_tools()
        call_kwargs = mock_class.return_value.post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer abc"


class TestCallToolConvenience:
    def test_call_tool_unknown_server(self) -> None:
        result = call_tool("missing", "add", {"a": 1, "b": 2}, config={"servers": {}})
        assert result["ok"] is False
        assert "unknown MCP server" in result["error"]

    def test_call_tool_no_url(self) -> None:
        config = {"servers": {"bad": {}}}
        result = call_tool("bad", "add", {"a": 1, "b": 2}, config=config)
        assert result["ok"] is False
        assert "no URL" in result["error"]

    def test_call_tool_success(self) -> None:
        response = _make_mock_response(200, {"jsonrpc": "2.0", "id": "1", "result": {"answer": 3}})
        with _patched_httpx([response]):
            result = call_tool("local_math", "add", {"a": 1, "b": 2}, config=SAMPLE_SERVERS)
        assert result["ok"] is True
        assert result["result"]["answer"] == 3


class TestAgentToolsIntegration:
    def test_mcp_tools_registered(self) -> None:
        from shared.agent_tools import TOOLS
        assert "mcp.list_servers" in TOOLS
        assert "mcp.call_tool" in TOOLS

    def test_mcp_list_servers_tool(self, tmp_path: Path) -> None:
        from shared.agent_tools import run_tool
        cfg_path = tmp_path / "mcp_servers.json"
        cfg_path.write_text(json.dumps(SAMPLE_SERVERS))
        with patch("shared.mcp_registry.load_servers_config", return_value=SAMPLE_SERVERS):
            result = run_tool("mcp.list_servers", {})
        assert result["ok"] is True
        assert len(result["servers"]) == 2

    def test_mcp_call_tool_agent(self) -> None:
        from shared.agent_tools import run_tool
        response = _make_mock_response(200, {"jsonrpc": "2.0", "id": "1", "result": {"answer": 5}})
        with patch("shared.mcp_registry.load_servers_config", return_value=SAMPLE_SERVERS), _patched_httpx([response]):
            result = run_tool("mcp.call_tool", {"server": "local_math", "tool_name": "add", "arguments": {"a": 2, "b": 3}})
        assert result["ok"] is True
        assert result["result"]["answer"] == 5

    def test_mcp_call_tool_missing_args(self) -> None:
        from shared.agent_tools import run_tool
        result = run_tool("mcp.call_tool", {"server": "local_math"})
        assert result["ok"] is False
        assert "tool_name required" in result["error"]

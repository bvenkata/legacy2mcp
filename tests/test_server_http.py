"""
`legacy2mcp run` over Streamable HTTP: a real uvicorn server on a free
local port, a real MCP client session over HTTP, and raw requests for what
the bearer-token check and the SDK's DNS-rebinding protection refuse.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from legacy2mcp.config import AuditConfig, ConfigError, Legacy2McpConfig, SecurityConfig, ServerConfig
from legacy2mcp.http_auth import BearerTokenMiddleware
from legacy2mcp.server import Legacy2McpApp

TOKEN = "s3cret-http-token-0123456789"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _app(**server) -> Legacy2McpApp:
    config = Legacy2McpConfig(
        server=ServerConfig(name="test-http", transport="http", port=_free_port(), **server),
        adapters=[],
        security=SecurityConfig(audit=AuditConfig(enabled=False)),
    )
    return Legacy2McpApp(config)


class _Running:
    """`app.http_app()` served by uvicorn in a thread, as `run_http()` serves it."""

    def __init__(self, app: Legacy2McpApp):
        self.app = app
        server = app.config.server
        self.url = f"http://127.0.0.1:{server.port}{server.path}"
        self._server = uvicorn.Server(
            uvicorn.Config(app.http_app(), host="127.0.0.1", port=server.port, log_level="warning", lifespan="on")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            assert time.time() < deadline, "uvicorn did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(10)
        self.app.close()


def _post(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(INITIALIZE).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


async def _tool_names(url: str, token: str | None = None) -> tuple[list[str], str]:
    import httpx2

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(headers=headers, timeout=10) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("describe_capabilities", {})
                return [t.name for t in tools.tools], result.content[0].text


@pytest.mark.asyncio
async def test_a_client_lists_and_calls_tools_over_http():
    with _Running(_app()) as running:
        names, capabilities = await _tool_names(running.url)

    assert names == ["describe_capabilities"]
    # the call went through the same dispatch as stdio: describe_capabilities' own answer
    assert json.loads(capabilities) == {"tool_count": 0, "tools": []}


@pytest.mark.asyncio
async def test_with_a_token_only_a_caller_that_sends_it_gets_in(monkeypatch):
    monkeypatch.setenv("TEST_HTTP_TOKEN", TOKEN)
    with _Running(_app(token_env="TEST_HTTP_TOKEN")) as running:
        missing = _post(running.url)
        wrong = _post(running.url, {"Authorization": "Bearer not-the-token"})
        other_scheme = _post(running.url, {"Authorization": f"Basic {TOKEN}"})
        names, _ = await _tool_names(running.url, TOKEN)

    for status, headers, body in (missing, wrong, other_scheme):
        assert status == 401
        assert headers.get("www-authenticate", headers.get("WWW-Authenticate")) == 'Bearer realm="legacy2mcp"'
        assert TOKEN not in body
    assert names == ["describe_capabilities"]


def test_a_request_naming_another_host_is_refused_on_localhost():
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends its own Host.
    with _Running(_app()) as running:
        status, _, _ = _post(running.url, {"Host": "evil.example"})
        good, _, _ = _post(running.url)

    assert status in (400, 403, 421)
    assert good == 200


def test_listening_beyond_localhost_needs_a_token_env():
    with pytest.raises(ValidationError, match="reachable from other machines: set server.token_env"):
        ServerConfig(transport="http", host="0.0.0.0")

    assert ServerConfig(transport="http", host="0.0.0.0", token_env="T").token_env == "T"
    assert ServerConfig(transport="http").host == "127.0.0.1"
    # stdio never listens, so its host is irrelevant
    assert ServerConfig(transport="stdio", host="0.0.0.0").transport == "stdio"


def test_the_path_must_be_absolute():
    with pytest.raises(ValidationError, match="server.path must start with '/'"):
        ServerConfig(transport="http", path="mcp")


def test_an_unset_token_env_is_a_config_error(monkeypatch):
    monkeypatch.delenv("UNSET_HTTP_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="'UNSET_HTTP_TOKEN' is not set"):
        ServerConfig(transport="http", token_env="UNSET_HTTP_TOKEN").http_token()


@pytest.mark.asyncio
async def test_the_token_check_passes_lifespan_events_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await BearerTokenMiddleware(inner, TOKEN)({"type": "lifespan"}, None, None)

    assert seen == ["lifespan"]


def test_run_refuses_http_when_the_token_variable_is_unset(tmp_path, monkeypatch, capsys):
    from legacy2mcp import cli

    monkeypatch.delenv("UNSET_HTTP_TOKEN", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  transport: http\n  token_env: UNSET_HTTP_TOKEN\n"
        "security:\n  audit:\n    enabled: false\nadapters: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["legacy2mcp", "run", "--config", str(config)])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    assert "'UNSET_HTTP_TOKEN' is not set" in capsys.readouterr().err

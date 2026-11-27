"""
The bearer-token check in front of `legacy2mcp run`'s HTTP transport.

stdio needs no auth: the only caller is the local process that spawned the
server. Over HTTP anything that can reach the port can call the tools, so
a server that listens beyond the local machine must have a token
(`server.token_env`, checked by config validation), and one on localhost
may have one. The token comes from an environment variable and is never
written to the config, the same way `dsn_env` keeps a database DSN out of
it.

One shared token is one caller: the audit log has no per-caller identity.
"""

from __future__ import annotations

import hmac
import json
from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


class BearerTokenMiddleware:
    """Answers 401 to an HTTP request without `Authorization: Bearer <token>`.
    The comparison is constant-time. Lifespan events pass through, so the
    MCP session manager still starts and stops with the server."""

    def __init__(self, app: ASGIApp, token: str):
        if not token:
            raise ValueError("BearerTokenMiddleware needs a non-empty token")
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            body = json.dumps({"error": "unauthorized", "detail": "a valid bearer token is required"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer realm="legacy2mcp"'),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, credential = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(credential.strip(), self._token)
        return False

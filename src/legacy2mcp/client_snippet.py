"""
Ready-to-paste MCP client connection config, generated from a just-written
config.yaml's real absolute path -- so "generate a server" and "wire it
into your AI client" are one flow instead of two (copying the shape by
hand out of README.md's "Client support" section, which this mirrors
exactly).

Every client here talks to legacy2mcp the same way: spawn `legacy2mcp run
--config <path>` over stdio. The JSON differs only in which key wraps the
server list (`mcpServers` vs `servers`) and whether a `type` field is
needed -- see docs/clients.md for the maintained source of truth
this is kept in sync with.
"""

from __future__ import annotations

import json
from pathlib import Path

_PIP_NOTE = (
    'Installed with pip/pipx instead of uv? Use "command": "legacy2mcp" and drop the '
    'leading "legacy2mcp" from "args" (or, for the Claude Code command, drop "uvx").'
)


def _run_args(config_path: str | Path) -> list[str]:
    return ["legacy2mcp", "run", "--config", str(Path(config_path).resolve())]


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def render_client_snippets(config_path: str | Path) -> str:
    """A short, copy-pasteable block for stderr -- not meant to be written
    to a file; the point is to see it right after the config it points at
    gets written, the same moment you'd otherwise go copy the shape out of
    the README by hand."""
    args = _run_args(config_path)
    claude_desktop_and_cursor = {"mcpServers": {"legacy2mcp": {"command": "uvx", "args": args}}}
    vscode = {"servers": {"legacy2mcp": {"type": "stdio", "command": "uvx", "args": args}}}

    lines = [
        "Connect an MCP client to this server:",
        "",
        "Claude Desktop (claude_desktop_config.json) / Cursor (.cursor/mcp.json):",
        _indent(json.dumps(claude_desktop_and_cursor, indent=2)),
        "",
        "VS Code (.vscode/mcp.json):",
        _indent(json.dumps(vscode, indent=2)),
        "",
        "Claude Code:",
        _indent("claude mcp add legacy2mcp -- uvx " + " ".join(args)),
        "",
        _PIP_NOTE,
    ]
    return "\n".join(lines)

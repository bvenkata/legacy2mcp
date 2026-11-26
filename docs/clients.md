# Connect legacy2mcp to your AI client

legacy2mcp is a stdio MCP server: the client launches it, so there is nothing to host. Every
snippet below runs the same command:

```bash
uvx legacy2mcp run --config /absolute/path/to/config.yaml
```

`uvx` (from [uv](https://docs.astral.sh/uv/)) downloads and runs the PyPI package on demand. If you
installed it with `pip` or `pipx`, replace `uvx legacy2mcp` with `legacy2mcp`.

Use an **absolute** path for `--config`. Clients launch the server from their own working
directory, so relative paths usually fail -- the exception is a project-scoped config
(`.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`) committed into the same repo you're
pointing it at, where the client's working directory is that repo's root. This repo ships
exactly that at [`.mcp.json`](../.mcp.json), [`.cursor/mcp.json`](../.cursor/mcp.json), and
[`.vscode/mcp.json`](../.vscode/mcp.json) -- all pointed at the bundled Petstore REST demo
(`examples/rest/config.petstore.yaml`), so opening this repo in Claude Code, Cursor, or VS
Code gets you a working `legacy2mcp` server with nothing to configure. Copy one into your
own project and point `--config` at your own `config.yaml` (absolute path, in that case).

## Try it first: zero-setup demos

Both demos need no credentials and no system of your own.

**REST (public Swagger Petstore):**

```bash
curl -O https://raw.githubusercontent.com/OWASP/legacy2mcp/main/examples/rest/config.petstore.yaml
```

Then use `/absolute/path/to/config.petstore.yaml` in any snippet below. Your client gets four
read tools (`petstore_find_pets_by_status`, `petstore_get_pet_by_id`, `petstore_get_inventory`,
`petstore_get_order_by_id`). `delete_pet` is hidden because it is a write.

**SOAP (sample Orders WSDL, runs locally):**

```bash
git clone https://github.com/OWASP/legacy2mcp.git && cd legacy2mcp
pip install -e ".[dev]"
python examples/soap/mock_orders_service.py &     # http://127.0.0.1:8124/orders
```

Then use `/absolute/path/to/legacy2mcp/examples/soap/config.orders.yaml`. You get
`orders_GetOrder`, `orders_ListOrders` and `orders_GetInventory`; `CreateOrder` and
`CancelOrder` stay hidden until you set `allow_write_operations: true`.

## Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit Config):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "legacy2mcp": {
      "command": "uvx",
      "args": ["legacy2mcp", "run", "--config", "/absolute/path/to/config.yaml"],
      "env": {
        "SERVICE_PASSWORD": "set-if-your-config-uses-password_env"
      }
    }
  }
}
```

Restart Claude Desktop after saving.

## Claude Code

```bash
claude mcp add legacy2mcp -- uvx legacy2mcp run --config /absolute/path/to/config.yaml
```

Pass secrets with `-e`: `claude mcp add legacy2mcp -e SERVICE_PASSWORD=... -- uvx legacy2mcp run --config ...`.
Add `--scope project` to write a shared `.mcp.json` for your team (leave secrets out of it), then
check with `claude mcp list`.

## Cursor

Create `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` for all projects:

```json
{
  "mcpServers": {
    "legacy2mcp": {
      "command": "uvx",
      "args": ["legacy2mcp", "run", "--config", "/absolute/path/to/config.yaml"]
    }
  }
}
```

## VS Code

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "legacy2mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["legacy2mcp", "run", "--config", "/absolute/path/to/config.yaml"]
    }
  }
}
```

## MCP Inspector

[MCP Inspector](https://github.com/modelcontextprotocol/inspector) is a validation tool, not an
AI client -- useful for confirming a config actually works before wiring it into one:

```bash
npx @modelcontextprotocol/inspector uvx legacy2mcp run --config /absolute/path/to/config.yaml
```

Opens a local web UI. Connect to the server, confirm the tools you expect are listed, run one
read-only tool, and check the matching entry in `legacy2mcp-audit.log`. The same loop -- tool
discovery, a real call, the audit log it produces -- also runs headlessly through the
Inspector's own `--cli` mode, e.g. `... --cli uvx legacy2mcp run --config ... --method
tools/list` (note: pass `--method` and any `--tool-name`/`--tool-arg` *after* the target
command, not before, or the Inspector's own argument parser swallows them).

## Secrets

Config files reference environment variable **names** (`password_env`, `token_env`, `dsn_env`),
never values, so the YAML is safe to commit. Set the variables in the client's `env` block (as in
the Claude Desktop example) or in your shell before launching the client.

## Troubleshooting

| Symptom | Check |
|---|---|
| Client shows the server as failed | Run the exact command in a terminal. Startup errors (bad WSDL URL, missing env var) print there. |
| No tools appear | Run `legacy2mcp inspect --config config.yaml`. Write-like operations are hidden unless `allow_write_operations: true`. |
| `uvx: command not found` | Install [uv](https://docs.astral.sh/uv/), or use `legacy2mcp` from `pip`/`pipx` and give the client its full path (`which legacy2mcp`). |
| Works in terminal, fails in the client | Use an absolute `--config` path, and put required env vars in the client's `env` block. |

## Verification status

[![Verified: Claude Desktop](https://img.shields.io/badge/Verified-Claude%20Desktop-7C3AED)](#claude-desktop)
[![Verified: Cursor](https://img.shields.io/badge/Verified-Cursor-000000)](#cursor)
[![Verified: VS Code](https://img.shields.io/badge/Verified-VS%20Code-007ACC)](#vs-code)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97706)](#claude-code)
[![Tested with MCP Inspector](https://img.shields.io/badge/Tested%20with-MCP%20Inspector-22C55E)](#mcp-inspector)
[![MCP compatibility](https://github.com/OWASP/legacy2mcp/actions/workflows/mcp-compatibility.yml/badge.svg)](https://github.com/OWASP/legacy2mcp/actions/workflows/mcp-compatibility.yml)

"Verified" here means a specific, narrow thing: the exact command + config
shape shown below was actually spawned and completed a real MCP
`initialize` handshake over stdio -- not just "MCP should be compatible."
For MCP Inspector, the loop was driven further: a real `tools/list`, a real
`tools/call`, and the resulting [audit log](security.md) entry, all
headlessly via the Inspector's own `--cli` mode. The last badge is what
keeps that claim honest over time rather than a one-time snapshot: a CI job
([`mcp-compatibility.yml`](../.github/workflows/mcp-compatibility.yml)) re-runs
that same `tools/list` / `tools/call` / audit-log loop on every push and
fails the build if the server can't start or exposes the wrong tools. Every
client below talks to `legacy2mcp` over **stdio only** -- there's no
HTTP/SSE transport yet (it's on the [roadmap](roadmap.md), not
shipped), so an `http://localhost:...` MCP URL won't work here.

| Client | Status | How it was verified |
|---|---|---|
| Claude Desktop | Verified | Spawned the exact `claude_desktop_config.json` command, completed a real `initialize` handshake, confirmed tools listed |
| Cursor | Verified | Same, via `.cursor/mcp.json`'s exact command |
| VS Code | Verified | Same, via `.vscode/mcp.json`'s exact command |
| Claude Code | Documented | Uses the identical launch command verified above via `claude mcp add`; that command itself wasn't independently re-run in this environment |
| MCP Inspector | Tested | Full CLI loop -- `tools/list`, `tools/call`, and the audit log entry it produced |

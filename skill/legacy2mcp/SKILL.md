---
name: legacy2mcp
description: >-
  Turns an existing SOAP/WSDL service, a database, or a REST API into a safe,
  typed, audit-logged MCP server using the open-source `legacy2mcp` CLI, so
  any MCP-aware agent can call it as a tool -- no hand-written adapter code
  or hand-maintained schemas. Use this whenever the user wants to connect an
  agent to a legacy or internal backend: they mention WSDL, SOAP, an
  internal/enterprise REST API, a database they want an agent to query or
  read from, "turn this API into MCP", "expose this service to
  Claude/Cursor/Copilot", or they start manually writing MCP server
  boilerplate for something that already has a WSDL, an OpenAPI/Swagger
  spec, or a database schema. Also reach for this if they're frustrated
  hand-rolling JSON Schemas for tool definitions from an existing API --
  that's exactly the problem this solves.
---

# legacy2mcp

`legacy2mcp` (`pip install legacy2mcp`, Apache-2.0, source at
https://github.com/OWASP/legacy2mcp) turns a legacy or internal system into
an MCP server by *introspecting* it rather than hand-describing it: it reads
the real WSDL, the real OpenAPI spec, the real database schema, or the real
route decorators in source code, and generates a config file plus a runnable
server from that. The reason to reach for it instead of writing an adapter by
hand is that the tool schemas it produces are always backed by something real
(a WSDL type, an introspected column, a typed route parameter) -- they can't
silently drift from the system they describe the way a hand-written JSON
Schema can.

It is read-only by default for every source type, and every tool call it
makes gets written to an audit log. Keep that posture in mind when deciding
whether to flip any write-enabling flag for the user -- don't do it without
them explicitly asking and understanding what it exposes.

## Decide the source type first

Ask yourself (or the user, if it's not obvious from what they've shared)
which of these they actually have, since the right `legacy2mcp init` command
depends entirely on it:

| They have... | Use |
|---|---|
| A live WSDL URL (a SOAP service) | `--wsdl-url` |
| A live REST API base URL | `--rest-url` |
| A website with no API docs at all (forms, content pages) | `--webpage-url` -- crawls it, every operation lands low-confidence/needs-review |
| A GitHub repo or local codebase (no live URL yet, or you want the schema derived from source) | `--github-url` / `--local-path` |
| A database they want queried/read from | the GitHub/local-path path, scanning the ORM models, **or** hand-write the `db` adapter config directly if there's no ORM to scan |
| A whole Python or JS/TS application and the question "what should agents be allowed to do?" | `legacy2mcp analyze <path> --no-ai`, not `init`. It writes an offline report of business flows, per-flow risk, proposed tools, agent topology and a phased roadmap. After the user reviews it and exports `selections.json`, run `legacy2mcp generate <out-dir> --selections selections.json --base-url <url>`. Write tools stay hidden unless `--allow-writes`. See [docs/analyze.md](https://github.com/OWASP/legacy2mcp/blob/main/docs/analyze.md) |

Read `references/sources.md` before running `init` for the first time in a
session -- it has the exact flags, what each path needs (or doesn't need) in
terms of an API key, and what to expect in the generated config for that
specific source type. Don't guess at flags from memory; the reference is
short and prevents easy mistakes like trying to pass an API key where none is
needed, or missing the one env var a DB config actually requires.

## The core workflow

1. **Install it** if it isn't already: `pip install legacy2mcp`. Confirm with
   `legacy2mcp --help` or check `pip show legacy2mcp` if there's any doubt.

2. **Generate the config.** Pick the flag per the table above and run
   `legacy2mcp init --out config.yaml` (see `references/sources.md` for the
   full command per source type). This writes `config.yaml` and, next to it,
   a `config.html` -- a plain-English report of exactly what tools the
   server will expose, written for a human to skim, not just YAML. Point the
   user at `config.html` if they want to sanity-check what got discovered
   without reading the config file.

3. **Review before trusting it.** `init` is a static/introspected best
   effort, not a verified result -- it annotates anything it couldn't
   confidently resolve with a `# TODO` comment directly in `config.yaml`
   (an unresolved base URL, an unverified parameter type, which endpoints it
   classified as writes). Read those TODOs and resolve them before moving
   on; don't just run the server against an unreviewed config, especially
   one with placeholder values still in it.

4. **See the real tool list before anyone calls anything:**
   `legacy2mcp inspect --config config.yaml`. This loads the config and
   prints the actual discovered MCP tools (name, JSON Schema, description)
   as JSON -- no server started, nothing called. If something looks wrong
   (missing tool, wrong type, a write operation that shouldn't be exposed),
   fix it in `config.yaml` and re-run `inspect` before going further.

5. **Wire it into whatever agent/client needs it.** Add an entry to that
   client's MCP server config (Claude Desktop, Cursor, Claude Code, VS Code,
   etc. all use the same shape):

   ```json
   {
     "mcpServers": {
       "<a-descriptive-name>": {
         "command": "legacy2mcp",
         "args": ["run", "--config", "/absolute/path/to/config.yaml"]
       }
     }
   }
   ```

   Always use an **absolute path** to `config.yaml` -- the client may launch
   the process from a different working directory than where you generated
   it. After adding this, the client restarts and launches the server itself;
   there's normally no reason to run `legacy2mcp run` by hand (it just sits
   there waiting for a client over stdio, which looks like nothing is
   happening if you run it directly in a terminal).

6. **Mention the built-in test chat** if `init` was run interactively (a
   real terminal, not a script): it already opened a local, no-API-key chat
   page in the browser automatically, with a clickable example question per
   discovered tool, so the user can sanity-check that a tool call actually
   reaches the real backend before wiring up a client at all. `--no-chat`
   skips it if that's not wanted.

## Safety posture to carry into every recommendation

- **Everything is read-only until explicitly opted into otherwise.**
  Write-like operations (anything that looks like it creates, updates, or
  deletes something) stay hidden from the agent unless
  `allow_write_operations: true` is set on that adapter in `config.yaml`.
  Don't set this for someone without confirming they understand it exposes
  real write access to a real backend.
- **Every tool call is audit-logged** (one JSON line per call: tool,
  arguments, timestamp, outcome) to `./legacy2mcp-audit.log` by default.
  Worth mentioning if the user is thinking about compliance/observability.
- **Database access is an explicit table/column allowlist, generated from
  the live schema** -- never arbitrary SQL assembled from a user prompt.
  If asked to add a new query, add a *named, parameterized* query to the
  `db` adapter's `queries` list in config, not free-form SQL.

## When this *isn't* the right tool

If the user wants to build a brand-new MCP server exposing tools that don't
correspond to an existing WSDL/REST API/DB schema/codebase -- i.e. there's
nothing to introspect -- `legacy2mcp` doesn't help; that's just writing an
MCP server from scratch with the official SDK. Reach for `legacy2mcp` only
when there's a real, already-existing system to point it at.

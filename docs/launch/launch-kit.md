# Launch kit (drafts, nothing here has been posted)

Copy, edit, post. Lead with the sample report: it's the hook.

## Show HN

**Title:** Show HN: legacy2mcp – map a codebase's business flows and risk, then generate MCP tools

**Body:**

I maintain legacy2mcp, an open-source (Apache 2.0, OWASP Incubator) CLI that
turned SOAP services, databases and REST APIs into MCP servers for AI agents.
The question people kept asking wasn't "how do I wrap this?" but "what should I
let an agent touch?"

So the new `legacy2mcp analyze` reads a Python or JS/TS codebase locally (ast
and tree-sitter, nothing executed, no API key) and produces one offline HTML
report with:

- every business flow, traced from its entry point through the code, with its side
  effects in order (DB writes, payments, email, external HTTP, queues)
- a risk label per flow (read-only / low-risk write / high-risk), with file:line
  reasons and a flag on writes with no visible auth
- proposed MCP tools (writes unticked by default), an agent topology with approval
  gates, and a three-phase roadmap

`legacy2mcp generate` then turns your selections into a server config that
validates every call, audit-logs it, and keeps write tools hidden unless you
opt in. AI enrichment is optional and bring-your-own-key. It shows a preview of
exactly what's sent (structure only by default, secrets redacted) and asks first.

Sample report from Miguel Grinberg's microblog: <link to docs/sample-report or hosted copy>
Repo: https://github.com/OWASP/legacy2mcp

It's static analysis, so dynamic dispatch and DI containers aren't followed.
"Read-only" means "no write found". I'd love reports of what it misses on your code.

## Reddit (r/Python, r/node, r/ExperiencedDevs, r/LocalLLaMA)

> I built a tool that reads your codebase and tells you which parts are safe to
> expose to AI agents: every flow traced, risk-labelled with file:line reasons,
> then generates an MCP server with writes off by default. Runs locally, no key
> needed. Sample report: <link>. Feedback on misses very welcome.

## Listings to update

- **MCP Registry** (`io.github.bvenkata/legacy2mcp`): `server.json` description
  once the release is tagged (the release workflow publishes it).
- **Glama**: refresh the description and add the sample report link.
- **awesome-mcp-servers / awesome-mcp lists**, one line:
  `legacy2mcp – Analyze a Python/JS codebase's business flows and risk, then generate read-only-by-default, audit-logged MCP servers; also wraps SOAP, databases and REST APIs.`

## Hosting the sample report

`docs/sample-report/index.html` is a single static file. Options once you
decide: enable GitHub Pages for `/docs` (the report then lives at
`https://owasp.github.io/legacy2mcp/sample-report/`), or upload it to
legacy2mcp.com.

## MCP-ready badge

See [badge.md](../badge.md).

## Demo recording

[`demo/analyze.tape`](../../demo/analyze.tape) is a VHS script:
`vhs demo/analyze.tape` renders `demo/analyze.gif`.

# `legacy2mcp analyze`, `report` and `generate`

Point legacy2mcp at a local Python or JavaScript/TypeScript codebase and it
tells you what the code does, which business flows exist, how risky each one
is, which MCP tools to build and in what order. It can then generate the MCP
server for the flows you approve.

```bash
pip install "legacy2mcp[analyze]"
legacy2mcp analyze ./my-app --no-ai          # offline, 0 tokens
open legacy2mcp-analysis/report.html          # review, tick tools, export selections.json
legacy2mcp generate legacy2mcp-analysis --selections selections.json \
  --base-url https://staging.my-app.example --out config.yaml
legacy2mcp run --config config.yaml           # or let your MCP client launch it
```

A sample report generated from a public open-source app is in
[`docs/sample-report/`](sample-report/).

## What it finds

| | Python (stdlib `ast`) | JavaScript / TypeScript (tree-sitter) |
|---|---|---|
| HTTP routes | Flask (incl. blueprints), FastAPI (incl. `APIRouter`), Django URLconfs (`include()`, class-based views) | Express (`app.get`, `router.route().get()`, `app.use()` mounts), Next.js pages router and app router |
| Other entry points | click / typer commands, Django management commands, Celery tasks, APScheduler and `schedule` jobs, pika consumers | commander commands, node-cron / node-schedule jobs, BullMQ workers, amqplib and kafkajs consumers |
| Side effects | SQLAlchemy, Django ORM, raw SQL (table names), PyMongo, `requests`/`httpx`/`aiohttp`, Stripe/Braintree, `smtplib`/Django mail/SendGrid, Celery/RQ queues, file writes, auth (`login_user`, `set_password`, JWT) | Prisma, Mongoose/Sequelize/TypeORM, raw SQL, `fetch`/axios, Stripe, nodemailer/Resend/SendGrid, BullMQ/amqplib/SQS, `fs` writes, jwt/bcrypt |
| Inputs | signature params, `request.args`/`request.form`/`request.GET`/`request.POST` | `req.body`/`req.query`/`req.params`, destructuring, `await request.json()`, `searchParams.get()` |

Handlers that branch on the HTTP method (`if request.method == "POST"`,
`form.validate_on_submit()`, `switch (req.method)`) are traced per method,
so a GET flow doesn't inherit its POST branch's writes.

## The pipeline

1. **Discovery** respects every `.gitignore`, a `.legacy2mcpignore` (same
   syntax) and `--exclude PATTERN`, and skips tests and migrations unless you
   pass `--include-tests`. Every exclusion is counted in the report's appendix.
2. **Parsing** happens per language. Nothing is imported, installed or executed.
3. **Graphs**: the call graph (internal calls, plus queue hand-offs as async
   edges), the module graph (imports) and the data-access graph (function → table, host, payment provider, queue, email, file, auth).
4. **Flows**: one per entry point, depth-first in source order, each function
   expanded once.
5. **Risk** is assigned by rules first: *read-only*, *low-risk write*, or *high-risk*
   (payments, database deletes, external HTTP sends, email, credential and
   token changes, HTTP DELETE). Each reason carries its `file:line`. A write
   with no visible authentication middleware or token check is flagged;
   sign-in, sign-up and password-reset routes are public by design and not flagged.
6. **AI enrichment** (optional, see below).
7. **Orchestration blueprint**: a coordinator, one specialist agent per
   business domain (at most 8), an operations agent for background flows, and an approval
   point on every write tool. Multimodal opportunities (documents, images,
   voice) are inferred from names, each with its evidence.
8. **Roadmap**: phase 1 is read-only tools, phase 2 is low-risk writes with
   approval, and phase 3 is orchestration. Each phase has effort, risk,
   prerequisites and exit criteria.
9. **Report**: `report.html`, one self-contained file.
10. **Generate**: approved tools become a `type: rest` config.

## The report

`report.html` opens offline from disk. Cytoscape.js is inlined and a
Content-Security-Policy blocks every network request. Sections: executive summary
and recommended first steps, codebase map, call-graph/data-flow view,
business flows, proposed MCP tools (with include toggles and a
**Export selections.json** button), agent topology, multimodal
opportunities, phased roadmap, and a methodology/scope appendix. It has search,
risk filters, light and dark themes, and a print-to-PDF layout. Link to one
flow with `report.html#flow=<flow-id>`.

`legacy2mcp report <analysis.json|dir> [--out file.html]` regenerates the
HTML from a saved `analysis.json`.

## AI enrichment (optional, bring your own key)

The offline result is complete. With a key, an LLM writes plain-English flow
names and descriptions, tool names and descriptions, review notes, agent
descriptions and orchestration advice.

```bash
pip install "legacy2mcp[analyze,ai]"
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY + --provider openai
legacy2mcp analyze ./my-app --ai-dry-run    # writes ai-payload.json, sends nothing
legacy2mcp analyze ./my-app                 # preview, confirm, send
```

| Flag | Meaning |
|---|---|
| `--provider anthropic\|openai` | default: whichever key is set (`anthropic` also works with an `ant auth login` profile when named explicitly) |
| `--model` | default `claude-opus-5-5` (Anthropic) / `gpt-4o-mini` (OpenAI) |
| `--max-tokens-budget N` | hard cap on input+output tokens (default 200,000; 0 = none); requests that would exceed it are skipped |
| `--ai-snippets` | also send up to 40 lines of each handler's source (redacted). Default: structure only |
| `--ai-dry-run` | write the exact payloads to `<out>/ai-payload.json` and stop |
| `--yes` | don't ask before sending (non-interactive runs without it stay offline) |
| `--api-key-env`, `--base-url` | custom key variable; OpenAI-compatible gateway |

Guarantees:
- **You see it first.** A preview shows the request count, estimated tokens and
  cost, and the number of redactions before anything is sent.
- **Minimal data.** By default it sends entry points, step labels, resource names,
  risk reasons and parameter names, never file contents.
- **Secret redaction.** Every payload is scanned for secrets by content (provider
  API keys, cloud/SaaS tokens, JWTs, bearer tokens, private keys,
  credentials in connection strings, `password = "..."`-style assignments)
  and scrubbed before sending.
- **Rules win.** AI never changes a risk level, a tool's safe flag, or what's
  selected by default. Invented flow ids are ignored, and names are sanitized
  and capped.
- **Failure is harmless.** A refusal, timeout, bad JSON or exhausted budget leaves the
  offline result in place.

## `generate`

```bash
legacy2mcp generate <analysis.json|dir> [--flows id,id] [--selections selections.json]
    [--base-url URL] [--allow-writes] [--auth-token-env VAR] [--server-name NAME] [--out config.yaml]
```

It selects tools from `--flows` (flow ids or tool names), the report's
`selections.json`, or by default the read-only tools. It then writes a config for
the existing REST adapter, so every tool gets JSON-Schema validation and
audit logging. Selected write tools are in the config but stay hidden until
`allow_write_operations: true` (`--allow-writes`). The config's `# TODO`
header lists what static analysis couldn't settle: structured request
bodies to spell out field by field, form-encoded endpoints, missing auth, the
base URL, and the human approval your MCP client should require for
high-risk tools (legacy2mcp audit-logs every call but doesn't pause for
approval). Check the result with `legacy2mcp inspect --config config.yaml`.

## `analysis.json`

`analysis.json` is versioned (`schema_version: "1"`) and deterministic. Paths are
repo-relative, lists are sorted, and run-specific values sit under `meta`, which
never contains an absolute local path. Top-level sections: `summary`, `scan`,
`modules`, `entry_points`, `graph`, `flows`, `tools`, `orchestration`,
`multimodal`, `roadmap`, `meta`. See `src/legacy2mcp/analysis/model.py`.

## Limits

This is static analysis. Calls through dynamic dispatch, dependency
injection containers, reflection, or variables whose type isn't visible
locally aren't followed, so "read-only" means "no write was found". Treat
the report as a map to review, not a proof. Python and JS/TS only for now;
other languages are on the [roadmap](roadmap.md).

## Extending it

Language analyzers implement a two-member protocol
(`analysis/analyzers/base.py`: `languages` and `analyze(source, text) -> FileResult`);
side-effect rules are plain functions in each analyzer
(`classify_call` / `classify_js_call`); risk rules live in `analysis/risk/rules.py`.
See [CONTRIBUTING.md](../CONTRIBUTING.md) and
[good-first-issues.md](good-first-issues.md).

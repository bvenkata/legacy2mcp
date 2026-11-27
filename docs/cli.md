# Command-line guide

Everything the `legacy2mcp` command can do beyond the quick start in the [README](../README.md). For whole-codebase analysis, see [analyze.md](analyze.md).

## Install

```bash
pip install legacy2mcp
# or:  uv tool install legacy2mcp   ·   pipx install legacy2mcp
```

Optional extras: `legacy2mcp[analyze]` for codebase analysis, `legacy2mcp[ai]`
for AI enrichment, and `legacy2mcp[db-postgres]` / `[db-mssql]` / `[db-oracle]`
for database drivers. legacy2mcp is also published to the
[MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.OWASP/legacy2mcp)
as `io.github.OWASP/legacy2mcp`.

## Generate a config automatically

`legacy2mcp init` asks what to point it at, then writes a starting
`config.yaml` for you:

```bash
legacy2mcp init
```

```
What do you want to point legacy2mcp at?
  1) WSDL URL (SOAP service)
  2) REST API URL (tries OpenAPI/Swagger discovery)
  3) A website with no API docs at all (crawls it for forms/content)
  4) GitHub repo URL or a local path to its source code
Choice [1-4]:
```

| Option | What happens | Needs an LLM key? |
|---|---|---|
| **WSDL URL** | Points a `soap` adapter at it directly -- `zeep` introspects the live WSDL at `inspect`/`run` time anyway, so nothing needs scanning up front. | No |
| **REST API URL** | Tries the common OpenAPI/Swagger spec locations (`/openapi.json`, `/swagger.json`, ...) against that URL and builds the `rest` adapter's endpoint list from the real spec if one's found; an empty scaffold otherwise. | No |
| **Website** | Crawls same-site pages starting there (`--max-pages`/`--max-depth` bound it, default 25/2) the same way `legacy2mcp discover --webpage-url` does, then goes straight to a config instead of stopping at a `contract.yaml`. Every operation still lands at `confidence: low` / `needs_review: true` in the generated TODOs -- going through `init` skips a file, not the review. | No |
| **GitHub repo / local path** | Clones (or reads) the repo and statically scans its Python source (AST only, never executed) for a `zeep.Client(...)` call, a SQLAlchemy/Flask-SQLAlchemy model, or Flask/FastAPI routes -- picking `soap`/`db`/`rest` accordingly. | Optional |

Each also works non-interactively via a flag -- `--wsdl-url`, `--rest-url`,
`--webpage-url`, `--github-url`, or `--local-path` -- for scripting or CI.
`--html-file` (flag only) does what `--webpage-url` does for one saved
page -- see [`--html-file`](#a-saved-html-page---html-file) below.
Only the GitHub/source-code path can use an LLM, and only to refine what
static analysis already found (ambiguous parameter types, read-vs-write
classification) -- never a dump of your repo. It needs no extra
dependency, just an OpenAI-compatible API key via `--llm-api-key-env`, or
an interactive hidden prompt if you omit that (never written to disk);
pass `--scan-only` to skip it outright and run static-analysis-only, same
as not entering a key.

```bash
legacy2mcp init --github-url https://github.com/you/your-api --scan-only --out config.yaml
```

Whichever path you take, review the generated file before running it --
it's marked up with `# TODO` comments for anything that couldn't be
confidently inferred (an unresolved `base_url`, an unverified param type,
which endpoints got classified `write: true`), and nothing in it is
verified against the real backend until you do:

```bash
legacy2mcp inspect --config config.yaml   # review the generated tools
legacy2mcp run     --config config.yaml   # start the MCP server
```

`legacy2mcp init` also writes a plain-English HTML report next to the
config (e.g. `config.yaml` -> `config.html`) -- open it in a browser to see,
in sentences rather than YAML, what tools the server will expose (every
REST endpoint / DB table / SOAP target it found, which ones can change data
and are gated behind `allow_write_operations`, and the review items above).
Pass `--html-out` to change where it's written, or `--no-html-report` to
skip it.

**Run `init` from a real terminal against a config that's already usable**
(a live `--wsdl-url`/`--rest-url`, or a scan that found something concrete)
**and it also opens a local test-chat page** in your browser, with a
clickable example question under every discovered tool (generated from
that tool's own name and schema, e.g. `getPetById` -> "get pet by id 4") so
you don't have to guess what to type. Click one, or type your own, and it
matches it to a discovered tool, calls the real adapter, and shows you the
real response. No AI/API key needed for this -- it's local keyword
matching against the tool names/descriptions, not an LLM, just enough to
sanity-check that the server actually works before you wire up a real MCP
client. It's skipped automatically when the config still has TODOs to fill
in (nothing to connect to yet), and never opens at all in
non-interactive/scripted runs. Pass `--no-chat` to opt out.

See [docs/security.md](security.md) for exactly what this heuristic
does and doesn't guarantee before pointing it at anything real.

## Discover a legacy interface before generating a server

`init` covers a live WSDL, a live REST URL, a website (`--webpage-url`),
and a repo it can scan -- all of those go straight to a config, no
separate step needed. For everything else -- a Postman collection someone
already built, an OpenAPI spec file sitting in a repo with no server
running yet, a database's stored-procedure catalog, a HAR capture of real
traffic, or queues you already know about -- there's an explicit step
first:

```bash
legacy2mcp discover --postman-file my-collection.json --out contract.yaml
legacy2mcp discover --openapi-file spec.yaml --out contract.yaml
legacy2mcp discover --har-file traffic.har --out contract.yaml
legacy2mcp discover --stored-procs --db-dialect postgres --db-dsn-env LEGACY_DB_DSN --out contract.yaml
legacy2mcp discover --queue-config queues.yaml --out contract.yaml
```

A website *can* go through `discover --webpage-url` too (see below) if you
want to review the contract before anything gets built -- `init
--webpage-url` runs the identical crawl, it just doesn't pause there. The
same goes for a saved page: `discover --html-file` or `init --html-file`.

Each writes a **Legacy Interface Contract** (`contract.yaml`): every
operation's inputs, outputs, enums, error codes, whether it reads or
writes, and a **confidence level** -- `high` for a live schema/catalog,
`medium` for a curated artifact like a Postman collection, `low` for
anything inferred from sampled traffic -- plus `needs_review` flags and
notes on anything uncertain. Review it like you'd review `config.yaml`'s
`# TODO`s.

Run that same command from a real terminal, though, and `discover` doesn't
stop at the contract: if the contract has anything invocable in it (not
every source does yet -- see below), it also writes a runnable
`config.yaml`, opens the same no-API-key test-chat page `init` can
auto-launch, and prints the same ready-to-paste MCP client JSON --
discover, try it, and get the connection snippet, in the one command you
already ran. Pass `--no-chat` to skip that and just get the contract (the
default in non-interactive/CI runs already), or `--no-write-config` to skip
only the `config.yaml` write.

Prefer to review the contract first before anything gets built or run? Skip
straight to either step yourself instead:

```bash
legacy2mcp init --from-contract contract.yaml --out config.yaml
legacy2mcp demo --from-contract contract.yaml
```

`demo` is the same "try it" step `discover` can auto-launch, just callable
on its own (also works from an existing `--config config.yaml`, not only a
contract) -- it saves a standalone `chat.html` pointing at the server it
just started (opened automatically unless `--no-open`), and prints the
client JSON once a config exists to point it at. The command has to keep
running (`Ctrl+C` to stop) for that page to work; it's a thin client page
with no backend of its own, so the real HTTP calls still have to be made by
*something*, same reason the browser can't just open a config file and
call a REST API directly.

A few things worth knowing:

- **No raw secret is ever written into a contract.** A Postman collection's
  saved bearer token, a HAR capture's `Authorization`/`Cookie` header
  values -- none of it gets persisted; only a note that auth was present,
  for you to configure yourself. The one controlled exception is a small,
  non-sensitive-looking **enum** (e.g. a `status` field that only ever took
  a handful of values across enough samples to mean something) -- see
  [docs/security.md](security.md).
- **For `--har-file`**, legacy2mcp never drives a capture proxy itself --
  point it at a HAR produced by anything: your browser devtools' "Export
  HAR", Postman's own traffic capture, or mitmproxy's built-in exporter
  (`mitmdump --set hardump=out.har`, or "Export as HAR" in the GUI). This
  is the lowest-confidence source (inferred from whatever was sampled, not
  a declared contract) -- every operation comes back flagged for review.
- **`--webpage-url`** is for a legacy web app or content site with no API
  docs at all -- just a URL. It crawls same-site pages from there
  (`--max-pages`/`--max-depth` bound it, default 25 pages / depth 2,
  never leaving the starting host), turning every `<form>` into an
  operation (a `<select>`'s options, or a shared-name radio/checkbox
  group, become an `enum`) and every form-less page with real content
  into a read-only "get this page" tool. No JavaScript is executed and
  nothing is ever submitted; non-GET forms get a note to confirm the
  backend accepts a JSON body (the REST adapter always sends one, even
  though a real browser would POST `application/x-www-form-urlencoded`).
  Same lowest-confidence posture as `--har-file` -- but unlike every
  other `discover` source, `base_url` fills in automatically (it's the
  host just fetched), so the test-chat page opens right away. Full crawl
  mechanics: [docs/security.md](security.md).
- **`--html-file`** runs the same form and content extraction on one saved
  HTML page, for a page `--webpage-url` can't reach: one behind a login,
  saved with the browser's "Save As" or captured by another tool. See
  [below](#a-saved-html-page---html-file).
- **Stored procedures and queue descriptions are documented, not runnable
  yet.** `--stored-procs` and `--queue-config` still produce real contract
  entries -- there's no adapter to actually *call* either today (see
  [docs/roadmap.md](roadmap.md)), so `init --from-contract` skips them
  with a clear TODO rather than pretending they work. Same reason
  `discover`'s auto-demo (above) has nothing to launch for a contract made
  up only of those -- it says so and just exits with the contract already
  written, rather than failing the whole command.
- **Every generated server also gets a `describe_capabilities` tool** for
  free -- a connected agent can call it to ask "what can this system do?"
  (every tool's description, side effect, confidence, required fields,
  known error codes) before calling anything for real.
- **`--out contract.html`** writes a human-readable report instead of the
  plain YAML -- the same operations, as a table, in your browser. It's
  still a valid `--from-contract`/`demo --from-contract` input: the full
  contract is embedded in the page (in a `<script type="application/json">`
  tag), not just summarized for reading.

### A saved HTML page (`--html-file`)

```bash
legacy2mcp discover --html-file saved-page.html --out contract.yaml
legacy2mcp init --html-file saved-page.html --out config.yaml
```

`--html-file` reads one saved page and runs the same extraction as
`--webpage-url`: each `<form>` becomes an operation, or a page with no form
but real text becomes a read-only "get this page" tool. It is in the same
mutually exclusive source group as `--webpage-url` on both commands.
Nothing is fetched and no link is followed. `--max-pages`/`--max-depth`
only bound the `--webpage-url` crawl, so passing either with `--html-file`
is an error.

A saved file doesn't record the URL it was served from, so:

- `base_url` stays `REPLACE_ME_BASE_URL` for you to fill in. A form whose
  `action` is an absolute URL gets a note naming that host.
- A relative `action` (`orders/search`) resolves against `/`, never against
  a `file://` URL or the file's local path, and the operation gets a note
  to confirm the real path. A form with no `action` (it posts back to the
  page itself) gets the placeholder path `/` and a note. A root-relative
  `action` (`/orders/search`) keeps its path.
- Every operation is `confidence: low` / `needs_review: true`, the same as
  `--webpage-url`.

Only the page's static HTML is read: if client-side JavaScript built a
form after the page loaded, save the page from the browser's DOM (for
example, devtools' "Copy outerHTML") rather than "View Source".

## Common commands

```bash
# Print the installed legacy2mcp version
legacy2mcp --version

# Generate a config from a live REST API (tries OpenAPI/Swagger discovery)
legacy2mcp init --rest-url "https://api.example.com" --out config.yaml

# Generate a config from a WSDL URL directly -- no scan needed
legacy2mcp init --wsdl-url "https://service.example.com/Legacy?wsdl" --out config.yaml

# Generate a config from a website with no API docs at all -- crawls it
legacy2mcp init --webpage-url "https://your-legacy-app.example.com" --out config.yaml

# The same for one page saved from the browser (e.g. behind a login) -- nothing is fetched
legacy2mcp init --html-file saved-page.html --out config.yaml

# Generate a config by statically scanning a GitHub repo (no AI, no API key)
legacy2mcp init --github-url https://github.com/you/your-api --scan-only --out config.yaml

# Same, with AI-assisted refinement of ambiguous parameter types / read-vs-write
legacy2mcp init --github-url https://github.com/you/your-api --llm-api-key-env OPENAI_API_KEY --out config.yaml

# Review a source before generating anything -- a Postman collection, OpenAPI
# file, HAR capture, stored-procedure catalog, or queue description
legacy2mcp discover --postman-file my-collection.json --out contract.yaml

# Try a config or contract in a chat page right now, no separate init step
legacy2mcp demo --from-contract contract.yaml
legacy2mcp demo --config config.yaml

# Map a whole local codebase (entry points, flows, risk, report) -- offline
legacy2mcp analyze ./path/to/repo --no-ai --out ./legacy2mcp-analysis

# Turn the approved flows into a runnable MCP server config
legacy2mcp generate ./legacy2mcp-analysis --selections selections.json --base-url https://staging.example.com

# Review the tools a config generates -- no server started
legacy2mcp inspect --config config.yaml

# Print the tool list as JSON, for scripting or a CI diff check
legacy2mcp inspect --config config.yaml > tools.json

# Show only one exact tool name
legacy2mcp inspect --config config.yaml --tool legacy_get_order

# Start the MCP server (stdio)
legacy2mcp run --config config.yaml

# Teach the AI coding agent working in *this* repo how to drive legacy2mcp
# (see "Teach your AI coding agent about legacy2mcp" below)
legacy2mcp install-skill
```

## Teach your AI coding agent about legacy2mcp

The rest of this guide is about the MCP servers `legacy2mcp` *produces*. This
one is different: it's for when an AI coding agent (Claude Code, Cursor,
Codex CLI, Gemini CLI, GitHub Copilot, ...) is helping you *build* the
integration in the first place, and you want it to already know how.

```bash
legacy2mcp install-skill
```

This writes short, command-first legacy2mcp instructions directly into the
current repo's own agent context files — a Claude Code slash command
(`.claude/commands/legacy2mcp.md`), a Cursor rule
(`.cursor/rules/legacy2mcp.mdc`), and a `## legacy2mcp` section appended (or
updated in place) in `AGENTS.md`, `GEMINI.md`, and
`.github/copilot-instructions.md`. The agent then already knows the core
workflow — which `init` flag fits which source, review the `# TODO`s before
trusting a generated config, check `inspect` before running anything live —
without you re-explaining it every session. It's idempotent: re-running it
replaces its own section rather than duplicating it, so it's safe to run
again after an upgrade. Review what it writes and commit it so your whole
team gets the same instructions (this repo does exactly that — see
[AGENTS.md](../AGENTS.md)).

There's also a standalone [`SKILL.md`](../skill/legacy2mcp/) in this repo,
written to the open [Agent Skills standard](https://www.agensi.io/learn/what-is-skill-md)
— the same know-how in a form marketplaces like [Agensi](https://www.agensi.io)
can list independently of any particular repo, for agents that discover
skills that way instead.

## Real-world usage

### In CI/CD — catch WSDL drift before it reaches production

`legacy2mcp inspect` loads the config, contacts the WSDL, builds every schema, and exits non-zero if anything fails:

```yaml
- name: Check the WSDL still generates valid MCP tools
  env:
    SERVICE_PASSWORD: ${{ secrets.SERVICE_PASSWORD }}
  run: |
    pip install legacy2mcp
    legacy2mcp inspect --config config/legacy.yaml > tools.json
    git diff --exit-code --no-index tools/legacy.snapshot.json tools.json  # optional: pin the contract
```

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | A config, source, or runtime error — bad WSDL/DB/REST config, a repo that couldn't be cloned, a missing required environment variable, and so on |
| `2` | Invalid command-line arguments (argparse's own default) |

### As a sidecar / long-running MCP server

`legacy2mcp run` speaks MCP over stdio. Package it with your config using the provided [`Dockerfile`](../Dockerfile) and let your MCP client launch it.

### In a data pipeline

Call the same generated, validated tools from your own code via any MCP client library to pull records on a schedule — the audit log records exactly what was fetched.

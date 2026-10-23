# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`legacy2mcp --version` / `-V`**: prints the installed package version and
  exits without needing a subcommand. The version comes from the installed
  distribution metadata, so it can't drift from `pyproject.toml`.

## [0.5.1] - 2026-10-23

### Added

- **`legacy2mcp init --webpage-url`**: a website now goes straight to a
  runnable config, same as `--wsdl-url`/`--rest-url` already did --
  `init` runs the identical crawl `discovery/webpage.py` does for
  `discover --webpage-url` (same `--max-pages`/`--max-depth` bounds),
  then skips straight to `classify.build_config_from_contract()` instead
  of stopping at a `contract.yaml` for a separate review step. Every
  operation still lands at `confidence: low` / `needs_review: true` in
  the generated TODOs -- going straight to `init` skips a file, not the
  review. `discover --webpage-url` is still there for anyone who wants
  to review the contract before anything gets built. Added to the
  interactive `legacy2mcp init` prompt as option 3 (bumping the repo
  option to 4).

## [0.5.0] - 2026-10-23

### Added

- **`legacy2mcp discover` now auto-launches the demo too**, not just
  `init`. In an interactive terminal, once the contract is written, if it
  has anything invocable in it, `discover` also writes a runnable
  `config.yaml` (`--write-config`/`--no-write-config`), starts the same
  no-API-key test-chat server `init` can auto-launch, and prints the same
  MCP client connection snippet (see below) -- `--no-chat` opts out (the
  default already, in non-interactive/CI runs). A contract with nothing
  invocable (stored-procs/queue-only) skips the demo with a clear message
  instead of failing the command -- the contract is still written either
  way.
- **A ready-to-paste MCP client connection snippet** (`legacy2mcp/
  client_snippet.py`), printed by `init` and `demo` (and now `discover`,
  above) right after a runnable `config.yaml` exists: the exact JSON
  shown in README.md's "Client support" section (Claude Desktop/Cursor,
  VS Code) plus the Claude Code `claude mcp add` one-liner, with the
  real absolute path already filled in -- connecting it to an AI client
  for real is a copy-paste, not a separate trip to the README.
- **`legacy2mcp demo --from-contract` now also writes a real
  `config.yaml`** alongside the chat page (`--write-config`/
  `--no-write-config`) -- previously the config it built only existed
  in memory for the demo server, so there was nothing on disk for an MCP
  client (or a later `legacy2mcp run`) to actually point at once the
  demo was done.

## [0.4.1] - 2026-10-22

### Fixed

- **`legacy2mcp init --out <path>.html` no longer silently corrupts the
  config it just wrote.** `--out` always has to be a loadable YAML
  config (`run`/`inspect`/`demo --config` all read it as one) -- an
  `.html`/`.htm` path there is now a clear error before anything is
  written, rather than being accepted and then clobbered: the
  auto-generated plain-English report defaults to the same `*.html`
  name as `--out` when `--out` already ends in `.html`, so the report
  write used to silently overwrite the just-written config with its own
  (non-YAML) content -- the next `inspect`/chat-launch step would then
  fail with a confusing YAML parse error pointing at the HTML markup.
  Also guarded the same collision defensively for an explicit
  `--html-out` that happens to match `--out`.

## [0.4.0] - 2026-10-22

### Added

- **`legacy2mcp demo`**: starts the same no-API-key test-chat server
  `init` can auto-launch, directly from a `--from-contract` contract or
  an existing `--config`, and saves a standalone `chat.html` pointing at
  it (opened automatically unless `--no-open`). The server still has to
  keep running (`Ctrl+C` to stop) for that page's `fetch()` calls to
  work -- it's a thin client page with no backend of its own, same
  reason `webui.py`'s existing same-origin page needed a server too; see
  `webui.render_page()`'s docstring. The server now also sends
  `Access-Control-Allow-Origin: *` on its JSON endpoints, since a saved
  `chat.html` opened as a `file://...` page is a different origin than
  the server and the browser enforces CORS even for localhost.
- **`legacy2mcp discover --out contract.html`**: writes a human-readable
  HTML report of the discovered contract instead of plain YAML (pick it
  by the `.html`/`.htm` extension on `--out`). It's still a valid
  `--from-contract`/`demo --from-contract` input, not just something to
  read -- the full contract is embedded in the page as JSON (escaped so
  a literal `</script>` inside any field value, e.g. a crawled page's
  text, can't be mistaken for the real closing tag).

## [0.3.2] - 2026-10-21

### Fixed

- **`init --from-contract` now fills in `base_url` automatically for a
  webpage-sourced contract.** A contract from `discover --webpage-url`
  already carries the live URL it fetched as its `source_label` -- that
  host is the real `base_url`, so there's no reason to leave
  `REPLACE_ME_BASE_URL` for a human to fill in by hand. Every other
  `discover` source (a local file, a database) is unaffected -- they
  still leave the placeholder, since there's no live host to infer it
  from. Practical effect: the generated config is immediately runnable,
  so `legacy2mcp init`'s no-API-key test-chat page now opens right away
  for a webpage-discovered server instead of requiring a manual edit
  first.

## [0.3.1] - 2026-10-21

### Changed

- **`--webpage-url` now crawls, instead of reading a single page.**
  Starting from the URL given, it follows same-site `<a href>` links
  (bounded by new `--max-pages`/`--max-depth` flags, default 25/2) and,
  for every page with no `<form>` but real text content, adds a
  read-only "get this page" operation whose tool returns the page's
  extracted text -- so a plain content site (no forms anywhere) now
  produces a usable contract instead of erroring with "no `<form>`
  elements found". Off-site links are never followed.
- **`adapters/rest_adapter.py`** now strips HTML down to plain text
  (`legacy2mcp/htmltext.py`) when a REST endpoint's response isn't JSON
  but is HTML, instead of returning the raw markup -- matters for the
  "get this page" tools above, and for any legacy REST endpoint that
  happens to answer with an HTML page.

## [0.3.0] - 2026-10-21

### Added

- **`legacy2mcp discover`**: an explicit "figure out what's there before
  generating a server" step, for sources `init` can't read directly --
  a Postman Collection (`--postman-file`), a local OpenAPI/Swagger spec
  file (`--openapi-file`), a database's live stored-procedure/function
  catalog (`--stored-procs`), a HAR traffic capture (`--har-file` -- from
  browser devtools, Postman, or mitmproxy's built-in HAR exporter;
  legacy2mcp never drives a capture proxy itself), a hand-written queue
  description (`--queue-config`), or a plain webpage with no API docs at
  all (`--webpage-url` -- fetches the page and turns every `<form>` in its
  static HTML into a REST operation, one field per input/select/textarea;
  no JavaScript executed, nothing ever submitted). Writes a Legacy
  Interface Contract (`contract.yaml`): every operation's inputs, outputs,
  enums, error codes, read/write side effect, and a confidence level, for
  human review.
- **`legacy2mcp init --from-contract`**: turns a reviewed contract into the
  same `config.yaml` every other `init` path produces. Stored-procedure
  and queue findings are documented in the contract but skipped (with a
  clear TODO) when building the runnable config, since no adapter can call
  either yet.
- **`describe_capabilities`**: every generated MCP server now exposes one
  extra, always-on, read-only meta-tool so a connected agent can ask
  "what can this system do?" (every tool's description, side effect,
  confidence, required fields, known error codes) before calling anything
  for real. Built from the live tool list, never a separate file read at
  serve time, so it can't drift out of sync with what's actually exposed.
- No raw secret or sampled value is ever persisted into a contract --
  only inferred shape. The one gated exception (a small, non-sensitive,
  sufficiently-repeated observed value set becoming an `enum`) is real
  JSON Schema enforcement when it reaches `config.yaml`, not just
  description. See [docs/security.md](docs/security.md)'s new
  `legacy2mcp discover` section for the full redaction policy.

## [0.2.6] - 2026-10-14

### Added

- `skill/legacy2mcp/` -- a [SKILL.md](https://www.agensi.io/learn/what-is-skill-md)
  for AI coding agents (Claude Code, Cursor, Copilot, etc.): teaches an
  agent when and how to use `legacy2mcp` itself -- picking the right
  `init` flag per source type, reviewing the generated config before
  trusting it, and carrying the read-only/audit-logged safety posture into
  its recommendations. `references/sources.md` has the per-source-type
  detail (SOAP/WSDL, REST, GitHub-repo scan, database).
- `legacy2mcp install-skill`: writes/updates legacy2mcp usage instructions
  directly into a project's own AI-assistant context files -- a Claude Code
  slash command (`.claude/commands/legacy2mcp.md`), a Cursor rule
  (`.cursor/rules/legacy2mcp.mdc`), and a `## legacy2mcp` section in
  `AGENTS.md` / `GEMINI.md` / `.github/copilot-instructions.md`. Idempotent
  (re-running updates its own section in place rather than duplicating it),
  same pattern other CLI tools (e.g. DockSec) use so an assistant already
  working in a repo picks up how to drive the tool without a separate
  marketplace lookup.

### Changed

- Upgraded `mcp` 1.29.1 -> 2.3.0. The SDK dropped the `@server.list_tools()`/
  `@server.call_tool()` decorators the server was built on in favor of
  constructor-registered `on_list_tools`/`on_call_tool` handlers that return
  `types.ListToolsResult`/`types.CallToolResult` directly; `server.py` is
  ported accordingly, including explicitly converting a failed tool call
  into an error *result* (`isError: true`) rather than letting it surface
  as a raw exception, which the old decorator used to do implicitly.
  Verified against a real `ClientSession` over the SDK's in-memory
  transport (a gap no prior test covered -- every other test called
  `Legacy2McpApp._dispatch()` directly, bypassing this wiring) and against
  a real `legacy2mcp run` process via the actual MCP Inspector CLI.
- Also bumped `oracledb`'s allowed range (`<3` -> `<27`, matching its real
  versioning scheme) and `actions/checkout`/`actions/setup-python` to v7 in
  CI.

## [0.2.5] - 2026-10-13

### Added

- The test chat page now shows a clickable "Try: ..." example question
  under each discovered tool, generated from that tool's own name/schema
  (e.g. `getPetById` with a required integer `petId` -> "get pet by id 4"),
  so it's obvious what to type for any API, not just the ones in the docs.

## [0.2.4] - 2026-10-13

### Added

- `legacy2mcp init`, run from a real terminal against a config that's
  already usable, now opens a local "try it" chat page in your browser --
  no AI/API key needed, since it's local keyword matching against the
  discovered tools' names/descriptions (not an LLM) that calls the real
  adapter and shows the real response. Skipped for configs that still have
  TODOs to fill in, and never triggered in non-interactive/scripted runs.
  `--no-chat` opts out.

## [0.2.3] - 2026-10-13

### Added

- `legacy2mcp init` now also writes a plain-English HTML report next to the
  generated config (e.g. `config.yaml` -> `config.html`) summarizing, in
  sentences rather than YAML, what tools the server will expose -- every
  REST endpoint / DB table / SOAP target found, which ones can change data
  and are gated behind `allow_write_operations`, and the review TODOs.
  `--html-out` changes where it's written; `--no-html-report` skips it.

### Fixed

- SOAP adapter: a WSDL exposing the same operation on more than one port of
  a service (the common ASMX/WCF pattern of a SOAP 1.1 port and a SOAP 1.2
  port with identical operations -- e.g. the public dneonline Calculator
  WSDL) crashed `legacy2mcp inspect`/`run` with "Duplicate tool name". The
  duplicate port's copy of an already-exposed operation is now skipped
  instead.

## [0.2.2] - 2026-10-13

- Demo GIF and banner/social-preview assets regenerated to show the REST
  adapter and `legacy2mcp init`, not just the original SOAP-only flow.
- Checked-in MCP client configs (`.mcp.json`, `.cursor/mcp.json`,
  `.vscode/mcp.json`), pointed at the bundled Petstore REST demo.
- `llms.txt` at the repo root.
- `.github/dependabot.yml` (pip + github-actions) and a committed `uv.lock`
  for the full dependency graph.
- A client-support badge row backed by `.github/workflows/mcp-compatibility.yml`,
  which re-runs a real `tools/list` / `tools/call` / audit-log check through
  MCP Inspector's `--cli` mode on every push.
- README: Quick start simplified to one copyable 3-command block, a "Why not
  just write an adapter by hand?" comparison section, and various accuracy
  fixes (numbering, stale labels, literal project name in the H1 and opening
  sentence for discoverability).
- No functional code changes since 0.2.1 -- cut purely to refresh the PyPI
  project page's rendered README/metadata.

## [0.2.1] - 2026-10-10

- Fixed: `server.json`'s description exceeded the MCP Registry's 100-character
  limit, so the registry publish step of the 0.2.0 release failed (PyPI
  publish had already succeeded). No code changes -- metadata-only patch so
  both channels ended up in sync.

## [0.2.0] - 2026-10-10

### Added

- **Database adapter** (`type: db`): introspects a live Postgres / SQL Server
  / Oracle schema and exposes `select_<table>` / `insert_<table>` tools for
  an explicit table/operation allowlist, plus named parameterized queries.
  No arbitrary SQL from the LLM, ever.
- **REST adapter** (`type: rest`): endpoint allowlist (method, path, typed
  params) from config, with bearer/basic/none auth. Needed because there's
  no live schema to introspect a plain REST API from the way there is for
  SOAP/DB.
- **`legacy2mcp init`**: generates a starting `config.yaml` from a WSDL URL
  (points a `soap` adapter at it directly), a REST API URL (tries
  OpenAPI/Swagger discovery), or a GitHub repo / local path (static AST scan
  for a SOAP client, an ORM model, or Flask/FastAPI routes). Optional
  OpenAI-compatible LLM assist refines only the ambiguous bits the scan
  flagged -- never a dump of the source -- and is skippable entirely with
  `--scan-only`.

## [0.1.0] - 2026-09-06

### Added

- Initial release: SOAP/WSDL adapter. Introspects any WSDL via `zeep`,
  generates one typed MCP tool per operation with a JSON Schema built from
  the WSDL's own XSD types, validates every call against that schema before
  it reaches the SOAP endpoint.
- Write-like operations (name-heuristic: `Create*`, `Update*`, `Delete*`, ...)
  excluded by default, opt-in via `allow_write_operations: true`.
  `include_operations` / `exclude_operations` allowlist/denylist on top.
- Basic auth support for the WSDL endpoint.
- Audit log: one JSON line per call (tool, arguments, timestamp, outcome).
- CLI: `legacy2mcp run --config ...` and `legacy2mcp inspect --config ...`.
- `stdio` transport.

[Unreleased]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/bvenkata/legacy2mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bvenkata/legacy2mcp/releases/tag/v0.1.0

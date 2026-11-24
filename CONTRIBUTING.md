# Contributing

New here? Start with an issue labeled
[`good first issue`](https://github.com/bvenkata/legacy2mcp/labels/good%20first%20issue)
-- small, self-contained, scoped to one file or one function. Looking for
something bigger? [`help wanted`](https://github.com/bvenkata/legacy2mcp/labels/help%20wanted)
issues are real gaps that need more design/discussion, not busywork. Either
way, comment on the issue before starting so two people don't duplicate
work, and feel free to ask questions on it -- that's what it's there for.

## What's next (short version)

The authoritative, up-to-date list is always the open
[`good first issue`](https://github.com/bvenkata/legacy2mcp/labels/good%20first%20issue)/
[`help wanted`](https://github.com/bvenkata/legacy2mcp/labels/help%20wanted)
issues, not this file -- but in short, the biggest gaps today are:

- **Queue adapter** (`type: queue`) -- `discover --queue-config` already
  captures queue descriptions into a contract; there's no runtime adapter
  to actually publish/subscribe yet.
- **Stored-procedure calling** -- `discover --stored-procs` already reads
  a live procedure/function catalog; calling one at runtime (same
  named-parameter posture the `db` adapter already uses for tables) is
  still future work.
- **A direct `init --db-dialect ... --db-dsn-env ...` entry point** --
  today a `db` config only comes from a repo scan (ORM detection) or a
  hand-written config; there's no live "point at a database and get a
  starter config" path the way `--wsdl-url`/`--rest-url`/`--webpage-url`
  already have.
- **HTTP/SSE transport** -- `stdio` only today.
- **More `analyze` coverage** -- new frameworks (Koa, Hapi, NestJS,
  Starlette, Litestar), new side-effect rules (more payment, email and queue
  SDKs), and the first analyzer for another language. These are the best
  first contributions: [docs/good-first-issues.md](docs/good-first-issues.md)
  has a list of small, well-scoped ones with exact file pointers.

Full version-by-version history: [docs/roadmap.md](docs/roadmap.md).

## Development setup

```bash
git clone https://github.com/bvenkata/legacy2mcp.git
cd legacy2mcp
pip install -e ".[dev]"
pytest tests/ -v
```

`[dev]` pulls in `pytest`, `pytest-asyncio`, `flask`, `lxml`, `pathspec`,
the tree-sitter grammars and the `anthropic` SDK -- enough to run the full
suite with no external services, network or API key (the AI tests use a
mocked provider). Tests run against an
in-process mock SOAP service (Flask), an in-process mock REST service
(Flask), and an in-process SQLite stand-in for the DB adapter -- no network,
no real Postgres/SQL Server/Oracle required. CI runs the suite on Python
3.10-3.12 ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), plus a
separate job ([`mcp-compatibility.yml`](.github/workflows/mcp-compatibility.yml))
that drives a real MCP `tools/list` / `tools/call` against the bundled demo
through MCP Inspector.

## Working on `legacy2mcp analyze`

The analysis pipeline lives in [`src/legacy2mcp/analysis/`](src/legacy2mcp/analysis/),
one package per stage, each producing a section of one typed `Analysis`
([`model.py`](src/legacy2mcp/analysis/model.py)):

| You want to... | Edit | Test with |
|---|---|---|
| Detect a new framework's routes / jobs / handlers | `analyzers/python.py` (`_decorator_entry_points`, `_call_entry_points`) or `analyzers/javascript.py` (`_call_entry_points`) | a snippet test in `tests/test_analysis_python.py` / `test_analysis_javascript.py` |
| Recognise a new side effect (an SDK call) | `classify_call` (Python) / `classify_js_call` (JS) | same files, `test_side_effects` |
| Change a risk rule | `risk/rules.py` | `tests/test_analysis_flows.py` |
| Add a language | a new `analyzers/<lang>.py` implementing the `Analyzer` protocol in `analyzers/base.py`, registered in `analyzers/__init__.py` | a fixture app under `tests/fixtures/analyze/` + golden file |
| Change the report | `report/assets/report.js` / `report.css` (plain JS, no build step) | `tests/test_analysis_report.py`, and `pytest tests/smoke` with Playwright installed |

Fixture apps under [`tests/fixtures/analyze/`](tests/fixtures/analyze/) each have a
golden `analysis.json` in [`tests/golden/analyze/`](tests/golden/analyze/). After an
intentional analyzer change, regenerate and **review the diff** like code:

```bash
UPDATE_GOLDEN=1 pytest tests/test_analysis_golden.py
git diff tests/golden
```

The report must stay a single offline file: no CDN links, no `fetch`. The
smoke test (`pip install playwright && python -m playwright install chromium`,
then `pytest tests/smoke`) fails on any console error or network request.

## Adding an adapter

New adapters for other legacy protocols (mainframe RPC, SAP RFC, message
queues, ...) are the highest-value contribution. The extension point is
[`BaseAdapter`](src/legacy2mcp/adapters/base.py): implement `discover_tools()`
(return a list of `ToolDefinition`s, each with a real JSON Schema) and
`invoke()` (validate-then-call). The server core in
[`server.py`](src/legacy2mcp/server.py) handles tool registration, argument
validation, dispatch, and audit logging for any adapter without changes.

The existing adapters are the reference examples:
[`soap_adapter.py`](src/legacy2mcp/adapters/soap_adapter.py),
[`db_adapter.py`](src/legacy2mcp/adapters/db_adapter.py), and
[`rest_adapter.py`](src/legacy2mcp/adapters/rest_adapter.py). A few
conventions they all share, worth keeping for a new one:

- Read-only by default. Anything that mutates state is excluded from
  discovery unless the config explicitly opts in (`allow_write_operations`
  plus, where relevant, a per-item `write: true`).
- The JSON Schema for a tool comes from the live/introspected source
  (a WSDL, a DB catalog, a discovered OpenAPI spec) wherever one exists --
  never hand-typed where it can be derived, so it can't silently drift.
- Adapter-level failures raise `AdapterError`, not a bare exception --
  `server.py` turns that into a clean error message instead of a stack
  trace reaching the caller.

## Tests

Mirror the pattern in `tests/test_<adapter>_adapter.py`: spin up the
in-process mock service as a module-scoped pytest fixture, build a
`Legacy2McpApp` against it, and assert on `discover_tools()` /
`app._dispatch(...)` directly rather than going through the MCP transport
layer. `tests/test_discovery_*.py` cover `legacy2mcp init`'s scanning and
config-generation logic with no network or LLM calls involved.

## Code style

No enforced formatter/linter is wired into CI yet -- match the existing
style: type hints throughout, `pydantic` models for config, dataclasses for
plain data, comments only where the *why* isn't obvious from the code
itself (a non-obvious constraint, a workaround, a subtlety a reader would
otherwise trip over) rather than restating what a line already says.

## Pull requests

- Keep the diff focused on one change; split unrelated cleanup into a
  separate PR.
- Add or update tests for behavior you change.
- Run `pytest tests/ -v` locally before opening the PR -- CI runs the same
  suite, plus the MCP compatibility check above.
- Update the relevant doc (`README.md`, `docs/*.md`, or `examples/README.md`)
  in the same PR as the behavior it describes, not as a follow-up.

## Reporting a bug or requesting a feature

Open a [GitHub issue](https://github.com/bvenkata/legacy2mcp/issues). For a
security issue, see [Reporting a vulnerability](docs/security.md#reporting-a-vulnerability)
instead of a public issue.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

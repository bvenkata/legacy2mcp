# v2 gap analysis: `legacy2mcp analyze`

This compares the repo as of `2d71934` (v0.5.1 plus unreleased changes)
against the v2 build plan, which adds a codebase-understanding pipeline:
analyze → flows → risk → report → generate.

## What exists today and can be reused

| v2 need | Existing code | Reuse |
|---|---|---|
| Walk a repo, parse Python | `discovery/scan.py`: `ast`-only walk, fixed skip-dir list, Flask/FastAPI route decorators, Pydantic body models, SQLAlchemy models, `zeep.Client` detection | Route and param extraction logic carries over into the Python analyzer. Its skip list (which skips `tests/` and `migrations/`) is too blunt for v2. |
| Read/write classification | `discovery/heuristics.py`: `looks_like_write()` checks name prefixes (create/update/delete/pay/charge/...) | Becomes one rule in the risk classifier. v2 also needs signals from call sites: DB writes, outbound HTTP, email, payment SDKs. |
| LLM call | `discovery/llm.py`: one OpenAI-compatible client over `requests`, only sees flagged routes, fails soft | Becomes the OpenAI provider behind a new provider interface. Anthropic is missing. |
| Redaction | `discovery/redact.py`: decides by field and header name which *values* never get persisted | Different problem. v2 needs secrets found by *content* (keys, tokens, connection strings) inside source snippets. That is new code. |
| Static HTML report | `discovery/report.py`, `generate.render_contract_html`: string templates, no JS | Escaping helpers and tone carry over. The interactive report is new. |
| Safe runtime | `config.py`, `server.py`, `adapters/rest_adapter.py`, `security/`: schema validation, audit log, writes off by default | **The key reuse for `generate`.** An analyzed Flask/FastAPI/Express app is an HTTP service, so approved flows can be emitted as a `type: rest` config and inherit every existing safety default with no new runtime code. |
| Repo resolution | `discovery/repo.py`: local path or shallow clone | Used as-is. |

## Gaps by milestone

| Milestone | Status | Main work |
|---|---|---|
| M1 Discovery + Python analyzer + analysis JSON | ~20% | `.gitignore` and `.legacy2mcpignore` support, language/framework detection, a full per-module unit model (functions, classes, imports, calls, side effects), Django URLconfs, a versioned JSON schema |
| M2 JS/TS analyzer + graphs | 0% | Express and Next.js API routes, import resolution, call graph, data-access graph |
| M3 Flows + rule-based risk | ~5% | Entry-point tracer, three-tier risk rules (`looks_like_write` is a seed) |
| M4 Interactive HTML report | 0% | Single-file report with an inlined graph library, themes, filters, print, include/exclude export |
| M5 AI providers + enrichment | ~15% | Provider interface (Anthropic + OpenAI), content-based secret redaction, preview/approve step, token estimate, `--max-tokens-budget`, mocked provider |
| M6 Orchestration, multimodal, roadmap | 0% | Mostly rule-built from flows and risk, with AI wording on top |
| M7 `generate` | ~60% via reuse | Flows plus include/exclude JSON → `rest` config (see above) |
| M8 Docs and launch | 0% | README rewrite, sample report, badge, CONTRIBUTING good-first-issues |

## Corrections to the plan

- **Repo and registry names.** The plan says `github.com/bvenkata/legacy2mcp`. This branch moves the project to `OWASP/legacy2mcp`. The registry name `io.github.bvenkata/legacy2mcp` stays.
- **No `serve` command.** The existing commands are `run`, `inspect`, `init`, `discover`, `demo` and `install-skill`. All of them stay unchanged. `analyze`, `report` and `generate` are new names, so they don't collide with anything.
- **"Scan command"** in the plan means `init --github-url/--local-path --scan-only`. It is not a separate command.
- **Releases.** Pushing a `v*` tag publishes to PyPI and the MCP Registry. A tag per milestone means a public release per milestone.

## Decisions needed before M1

1. **Python parser.** The plan says tree-sitter for both languages. `ast` is in the standard library, more precise for Python, and already used by `scan.py`. *Recommendation:* use `ast` for Python and tree-sitter only for JS/TS.
2. **Dependencies.** The core install is light today (zeep, mcp, pydantic, ...). *Recommendation:* add a `legacy2mcp[analyze]` extra with `pathspec`, `tree-sitter`, `tree-sitter-javascript` and `tree-sitter-typescript`. The `--no-ai` Python-only path then needs only `pathspec`. LLM calls stay on `requests`, so no SDK dependencies.
3. **Graph library in the report.** Either vendor `cytoscape.min.js` (MIT, about 400 KB) as package data and inline it, or hand-write a small SVG force layout. *Recommendation:* vendor Cytoscape, since zoom, drag and layout are what the plan asks for.
4. **What `generate` emits.** Either a `type: rest` config for the existing runtime, or standalone server code. *Recommendation:* a config, because it reuses the audited safety layer instead of forking it.
5. **Package layout.** *Recommendation:* put everything under `src/legacy2mcp/analysis/` (`analyzers/`, `graph/`, `flows/`, `risk/`, `ai/`, `orchestration/`, `roadmap/`, `report/`, `generate/`) so it stays clearly apart from `discovery/`.
6. **HTML smoke test.** Checking for "no console errors" needs a headless browser. *Recommendation:* the default test suite checks for no external URLs plus a JS syntax parse, and a separate CI job runs Playwright.

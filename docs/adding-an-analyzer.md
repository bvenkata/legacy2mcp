# Adding a language or framework to `legacy2mcp analyze`

`legacy2mcp analyze` understands Python and JavaScript/TypeScript today. Many
legacy systems are written in something else: Java, C#, PHP, Ruby, VB6, COBOL.
Each new language analyzer lets those teams map their business flows and generate
a safe MCP server the same way.

Before you start, open a
[New language or framework](https://github.com/OWASP/legacy2mcp/issues/new?template=new-analyzer.yml)
issue (or comment on an existing one) so the work isn't duplicated.

## Smaller first: a new framework in an existing language

Most contributions start here, and they're a good way to learn the pipeline.

| You want to... | Edit | Test with |
|---|---|---|
| Detect a framework's routes, jobs or handlers | `analyzers/python.py` (`_decorator_entry_points`, `_call_entry_points`) or `analyzers/javascript.py` (`_call_entry_points`) | a snippet test in `tests/test_analysis_python.py` / `test_analysis_javascript.py` |
| Recognise a new side effect (an SDK call that writes, sends or pays) | `classify_call` (Python) / `classify_js_call` (JS) | same files, `test_side_effects` |
| Change a risk rule | `risk/rules.py` | `tests/test_analysis_flows.py` |

## A new language

The pipeline is language-neutral after parsing. A language analyzer only has to
turn one source file into a `FileResult`; call graphs, flows, risk, tools, the
report and `generate` then work unchanged.

### 1. Map file extensions

Add the extensions to `LANGUAGE_BY_EXT` in
[`analysis/walk.py`](../src/legacy2mcp/analysis/walk.py), e.g. `".java": "java"`.

### 2. Implement the `Analyzer` protocol

Create `src/legacy2mcp/analysis/analyzers/<language>.py`. The protocol is in
[`analyzers/base.py`](../src/legacy2mcp/analysis/analyzers/base.py):

```python
class JavaAnalyzer:
    languages = ("java",)

    def analyze(self, source: SourceFile, text: str) -> FileResult:
        ...
```

`FileResult` carries, for that one file:

- a `Module` ([`model.py`](../src/legacy2mcp/analysis/model.py)): its functions,
  each with `calls` (callee names as written), `side_effects` (kind
  `db`/`http_out`/`email`/`payment`/`queue`/`file`/`auth`, op `read`/`write`) and
  `decorators` (or annotations / attributes);
- `entry_points`: HTTP routes, CLI commands, scheduled jobs and message
  handlers, each with method, path, params and wrapping middleware;
- `mounts`: route tables mounted under a prefix elsewhere (resolved later);
- `frameworks` detected.

Rules:

- **Parse, never execute.** Use a real parser (the stdlib `ast` for Python,
  tree-sitter grammars for JS/TS). Never import, compile or run the scanned code.
- If the parser is a third-party package, import it lazily and add it to the
  `analyze` extra, with an `available()` check like
  [`analyzers/javascript.py`](../src/legacy2mcp/analysis/analyzers/javascript.py).
- A file that fails to parse sets `Module.parse_error`; it must not crash the run.

### 3. Register it

Add it to `default_analyzers()` in
[`analyzers/__init__.py`](../src/legacy2mcp/analysis/analyzers/__init__.py).

### 4. Add a fixture app and golden file

Create a small but realistic app under `tests/fixtures/analyze/<name>/` with a
read-only route, a write, a high-risk flow (a payment, a delete or an outbound
send) and one unauthenticated write. Then generate and **review** its golden file:

```bash
UPDATE_GOLDEN=1 pytest tests/test_analysis_golden.py
git diff tests/golden
```

Add snippet tests for each framework pattern you detect.

### 5. Document it

Update the "What it finds" table in [`docs/analyze.md`](analyze.md), the
"Supported frameworks" list in the README, and [`CHANGELOG.md`](../CHANGELOG.md).

## Checklist

- [ ] Nothing in the scanned repo is imported, compiled or executed.
- [ ] Every side effect and risk reason points at a real `file:line`.
- [ ] Parse failures are recorded, not fatal.
- [ ] Fixture app and golden file added and reviewed.
- [ ] `pytest tests/ -q` passes offline.

## Getting help

Ask in the issue for your language, in
[GitHub Discussions](https://github.com/OWASP/legacy2mcp/discussions), or on OWASP
Slack in [`#project-legacy2mcp`](https://owasp.org/slack/invite).

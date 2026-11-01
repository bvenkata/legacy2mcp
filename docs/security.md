# Security model

This document says plainly what protections exist today and what
doesn't, so you can make an informed decision about where to point
this at.

## DB adapter (v0.2)

1. **No arbitrary SQL from the LLM, ever.** This is a hard design
   constraint, not a configurable option. The LLM can only call
   `select_<table>` / `insert_<table>` for tables explicitly
   allowlisted in config, or a named `query_<name>` tool whose SQL
   text was written by whoever owns the config file -- the LLM only
   ever supplies the placeholder *values* a named query declares in
   `params`, never SQL text.
2. **Columns are introspected from the live schema**, not
   hand-maintained in config, so a tool's JSON Schema can't silently
   drift from what the table actually looks like.
3. **Inserts and write-marked named queries require
   `allow_write_operations: true`** on top of being explicitly listed
   -- the same "read-only by default, explicit opt-in for writes"
   posture as the SOAP adapter's `allow_write_operations` flag.
4. **Connection strings never live in the config file.** They're read
   from an environment variable named in `dsn_env`.
5. **Row counts are capped** (`max_rows` per table/query) and enforced
   in the adapter after the query runs, as a safety cap against an
   unexpectedly huge result set -- not a pagination feature.

What it does *not* do yet: no column-level data masking (if a column
is allowlisted, its values are returned as-is), no query timeout
enforcement beyond the driver's own connect timeout, and the
SQL Server / Oracle dialects have less real-world testing than
Postgres (see `src/legacy2mcp/adapters/db_dialects.py`) -- review the
generated `inspect` output against your actual schema before trusting
it in production.

## REST adapter + `legacy2mcp init`

1. **There is no live schema for a plain REST API** the way there is a
   WSDL or a database catalog -- the `rest` adapter trusts its config's
   endpoint list, method, path, and param shapes as-is. Nothing about a
   hand-written or `legacy2mcp init`-generated `config.yaml` is verified
   against the real backend until you actually call it.
2. **`legacy2mcp init`'s read/write classification is a heuristic, not
   semantic analysis**, same caveat as the SOAP adapter's write-verb
   check: it defaults to "non-`GET` is a write", optionally refined by an
   LLM's best guess from the route name/path/params. A `GET` endpoint
   that mutates state, or a `POST` that's actually a search, would not be
   caught. Review every `write:` flag in the generated file -- it only
   ever controls what *could* be exposed; `allow_write_operations` always
   generates as `false`.
3. **Parameter types the scanner couldn't confidently infer default to
   `string`** and are left as a `# TODO` in the generated file, not
   silently guessed as something more specific.
4. **The LLM step, when enabled, sends only the already-detected
   endpoint names/paths/params it flagged as ambiguous** to the
   configured OpenAI-compatible API -- never a dump of the repo's
   source. It's still a third-party API call; use `--scan-only` if that
   repo's structure itself shouldn't leave your machine.
5. **The LLM API key and any GitHub token are read from an environment
   variable name, or typed at a hidden interactive prompt** -- never as
   a plain CLI argument, and never written to the generated config or
   to disk.
6. **`legacy2mcp init` clones the target repo and parses it with `ast`
   only** -- it never executes any code from the scanned repo.

## `legacy2mcp discover`

1. **No raw sampled or curated value is ever written into a contract.**
   A Postman collection's saved bearer token, a HAR capture's
   `Authorization`/`Cookie` header values, any other field value read from
   a real request/response -- none of it is persisted. Only *inferred*
   shape (type, required-ness, location) is kept, plus a note when auth was
   detected (e.g. "source declared bearer auth -- configure
   RestAdapterSettings.auth yourself"), never the credential itself.
2. **The one deliberate exception is `enum`**, and it's narrowly gated
   (`src/legacy2mcp/discovery/redact.py::enum_eligible`): a field's observed
   values are only ever recorded as an enum if its name doesn't match a
   sensitive-name pattern (`password`, `secret`, `token`, `api_key`,
   `ssn`, `credit_card`, ...) **and** there were few enough distinct values
   to plausibly be a real closed set (≤10). For `--har-file` specifically,
   there's an additional minimum-sample gate (`_MIN_SAMPLES_FOR_ENUM` in
   `discovery/har.py`) -- with only 1-2 sampled requests, *every* field
   trivially has "few distinct values", which doesn't mean anything; a
   candidate enum is only considered once enough samples have actually
   repeated a small set of values.
3. **`--har-file` never reads header values at all**, only header *names*
   (to produce the auth note) -- so there's no redaction-correctness
   dependency on legacy2mcp's own sensitive-header list catching every
   possible secret-carrying header name; the value is simply never looked
   at in the first place.
4. **Every HAR-derived operation is unconditionally `confidence="low"` and
   `needs_review=True`**, regardless of how confident any individual
   heuristic feels -- it's inferred from whatever traffic happened to be
   sampled, never treated as a declared contract.
5. **Stored-procedure introspection connects to the live database** the
   same way the `db` adapter does (driver imported lazily, DSN read from
   an environment variable named by `--db-dsn-env`, never written to
   disk) -- but only to read the procedure/function catalog; no procedure
   is ever invoked by `discover`.
6. **`--webpage-url` crawls same-site pages only, bounded, and never
   executes anything.** Starting from the URL you give it, it follows
   `<a href>` links found in each page's static HTML, up to `--max-pages`
   (default 25) and `--max-depth` (default 2) -- both bounds exist
   specifically so this stays a small, deliberate crawl and never an
   open-ended one. A link is only ever followed if it resolves to the
   *same host* as the URL you gave (`www.`-prefix differences are treated
   as the same host; a different host entirely, including a form's own
   `action`, is never fetched) -- it cannot wander off to crawl a
   different site. No JavaScript is executed on any page, and no `<form>`
   it finds is ever submitted. A hidden input's *value* is never
   persisted (it may be a per-request token such as CSRF, which a static
   scan can't resolve anyway) -- only its field name, flagged
   `needs_review`. A page with no form becomes a read-only "get this
   page" operation whose only output is that page's extracted text (see
   `legacy2mcp/htmltext.py`) -- never anything beyond what the page's own
   static HTML already made public. Like `--rest-url`'s OpenAPI probe,
   this does fetch URLs you point it at, so the same caution that applies
   to pointing `legacy2mcp` at any live endpoint applies here too -- only
   point it at a site you're authorized to crawl.

What it does *not* do yet: path-templating for `--har-file` and
`--webpage-url` is a simple per-entry heuristic (numeric/UUID-shaped
segments become a path param) with no cross-sample agreement -- two
genuinely different endpoints that happen to template to the same path
get merged into one operation; review the result. `--webpage-url` also
can't see a form that client-side JavaScript builds or modifies after
load -- only what's in the page's static HTML.

## What v0.1 actually does

1. **Every tool call is validated against a JSON Schema before it
   reaches the SOAP endpoint.** The schema is derived from the WSDL's
   own XSD types (see `src/legacy2mcp/schema/xsd_to_jsonschema.py`),
   so an LLM can't send a string where the WSDL declares an integer,
   can't omit a required field, and (schemas are generated with
   `additionalProperties: false`) can't smuggle in extra fields the
   operation doesn't expect.
2. **Write-like operations are excluded by default.** Operation names
   are checked against a list of write-suggesting verb prefixes
   (`Create`, `Update`, `Delete`, `Cancel`, `Submit`, `Void`, `Pay`,
   ...) at discovery time. An adapter only exposes those tools if you
   set `allow_write_operations: true` on it explicitly. This is a
   heuristic, not a guarantee -- see "What this does NOT do" below.
3. **Every call is audit-logged.** Tool name, arguments, identity (if
   available), timestamp, and success/failure are written as one JSON
   line per event to the path in `security.audit.path`. Good enough to
   answer "who called what, with what arguments, and did it succeed"
   during an incident review.
4. **Credentials never live in the config file.** Basic auth passwords
   are read from an environment variable named in `password_env`, not
   written into YAML.
5. **SOAP faults don't leak raw stack traces to the caller.** They're
   caught and re-raised as a plain error message.

## What this does NOT do (yet)

- **No authentication or authorization on the MCP server itself.**
  Anything that can spawn/connect to the server can call any exposed
  tool. If you need per-caller identity and role-based tool access,
  that's on the [roadmap](roadmap.md) (v0.2) but not implemented --
  don't expose a v0.1 server to untrusted callers.
- **The write-operation filter is a name heuristic, not semantic
  analysis.** A WSDL operation named `ProcessRecord` that actually
  deletes data would not be caught by the `_looks_like_write()` check.
  Review `include_operations` / `exclude_operations` explicitly for
  any adapter pointed at a system where a wrong call has real
  consequences -- don't rely on the heuristic alone.
- **No rate limiting.** A misbehaving or malicious caller can call a
  tool as fast as the underlying SOAP service allows.
- **No TLS/mTLS configuration is provided for the WSDL endpoint**
  beyond whatever `zeep`/`requests` do by default over HTTPS. If your
  legacy system needs client certificates, you'll need to extend
  `SoapAdapter._get_client()` yourself for now.
- **No encryption at rest for the audit log.** It's a plain JSONL
  file; arguments (which may include business data) are logged as-is.
  Point `security.audit.path` somewhere with appropriate file
  permissions, and don't point it at a WSDL whose arguments contain
  secrets you don't want written to disk.

## Reporting a vulnerability

Please open a GitHub issue marked `security`, or contact the
maintainer directly if the issue is sensitive enough that a public
issue isn't appropriate.

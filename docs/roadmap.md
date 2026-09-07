# Roadmap

## v0.1 (this release)

- SOAP/WSDL adapter: introspects any WSDL via `zeep`, generates one MCP
  tool per operation with a real JSON Schema built from the WSDL's XSD
  types, validates every call against that schema before invoking the
  SOAP endpoint, and audit-logs every call (start/success/error) to a
  local JSONL file.
- Safety defaults: operations whose names look like writes
  (`Create*`, `Update*`, `Delete*`, `Cancel*`, `Submit*`, ...) are
  excluded unless `allow_write_operations: true` is set explicitly on
  that adapter. `include_operations` / `exclude_operations` allowlists
  on top of that.
- `stdio` transport only (the transport Claude Desktop and most MCP
  clients spawn servers over).
- Basic auth support for the WSDL endpoint (`auth.type: basic`).
- CLI: `legacy2mcp run --config ...` and `legacy2mcp inspect --config ...`
  (dry-run tool discovery, no server started -- useful in CI to catch
  a broken WSDL before deploying).

## v0.2 (planned, not implemented)

- **Database adapter** (`type: db`): introspect a Postgres/SQL
  Server/Oracle schema and expose `select_<table>` / `insert_<table>`
  tools for an explicit allowlist of tables and operations, plus
  named parameterized queries defined in config. No arbitrary SQL
  from the LLM, ever -- this is a hard design constraint, not a
  configurable option.
- **HTTP/SSE transport**, for MCP clients that talk HTTP instead of
  spawning a stdio subprocess.
- Fine-grained authorization: map an identity's role or attributes to
  an allowed tool-name pattern (`select_*`, `get_*`, etc.).
- OAuth2 auth for SOAP endpoints (basic auth only in v0.1).

## v0.3+ (ideas, unscheduled)

- **Queue adapter** (`type: queue`): publish/subscribe tools for
  Kafka, RabbitMQ, SQS.
- Multi-step workflow composition with human-in-the-loop approval
  gates (chain several tools into one guarded operation).
- OpenTelemetry tracing export (spans per tool call) alongside the
  existing file-based audit log.
- Community-contributed adapters for other legacy protocols (mainframe
  RPC, SAP RFC, etc.).

Contributions on any of the above are welcome -- see CONTRIBUTING.md
(coming soon) or open an issue describing the adapter you want to add.
The `BaseAdapter` interface in `src/legacy2mcp/adapters/base.py` is the
extension point: implement `discover_tools()` and `invoke()`, and the
MCP server core in `server.py` handles the rest (validation, dispatch,
audit logging) without any changes.

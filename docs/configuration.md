# Configuration

Hand-written `config.yaml` examples for each adapter, and every config key. `legacy2mcp init` and `legacy2mcp generate` write these files for you; see [cli.md](cli.md).

## Point it at your own WSDL

```yaml
# config.yaml
server:
  name: my-legacy-mcp

adapters:
  - id: legacy
    type: soap
    config:
      wsdl_url: "https://service.example.com/LegacyService?wsdl"
      auth:
        type: basic
        username: "svc-account"
        password_env: "SERVICE_PASSWORD"   # value read from the environment, never the file
      allow_write_operations: false        # Create*/Update*/Delete*/… stay hidden
      include_operations: ["GetRecord", "GetRecordDetails", "SearchRecords"]

security:
  audit:
    enabled: true
    path: "./legacy2mcp-audit.log"
```

```bash
export SERVICE_PASSWORD=...
legacy2mcp inspect --config config.yaml   # review the generated tools
legacy2mcp run     --config config.yaml   # start the MCP server (stdio)
```

A production-shaped template with comments lives at [`examples/soap/config.template.yaml`](../examples/soap/config.template.yaml).

## Point it at your own database

```bash
pip install "legacy2mcp[db-postgres]"   # or db-mssql / db-oracle
```

```yaml
# config.yaml
adapters:
  - id: orders
    type: db
    config:
      dialect: postgres             # postgres | mssql | oracle
      dsn_env: "ORDERS_DB_DSN"      # connection string, read from the environment
      allow_write_operations: false # insert_<table> / write: true queries stay hidden
      tables:
        - name: orders
          operations: [select]
          columns: [id, status, total, created_at]   # column allowlist
          filterable_columns: [status]                # equality filters on select_orders
          max_rows: 200
      queries:
        - name: orders_by_status_since
          sql: "SELECT id, status, total FROM orders WHERE status = :status AND created_at >= :since"
          params:
            - { name: status, type: string, required: true }
            - { name: since, type: string, required: true }
```

```bash
export ORDERS_DB_DSN=...
legacy2mcp inspect --config config.yaml   # review the generated tools
```

Columns are introspected from the live schema at startup, the same way SOAP operation
schemas come from the WSDL -- there's nothing to hand-maintain. **There is no path from
the LLM to arbitrary SQL, ever**: it can only call `select_<table>` / `insert_<table>` for
tables listed under `tables:`, or a `query_<name>` tool whose SQL you wrote under
`queries:` -- the LLM supplies only the placeholder values a query's `params` declare,
never SQL text. A production-shaped template lives at
[`examples/db/config.template.yaml`](../examples/db/config.template.yaml).

## Point it at your own REST API

There's no WSDL or live database schema to introspect a plain REST/JSON API
from, so the `rest` adapter's endpoint list is explicit config -- write it
by hand, or generate a starting point with `legacy2mcp init` (see
[cli.md](cli.md#generate-a-config-automatically)).

```yaml
# config.yaml
adapters:
  - id: orders
    type: rest
    config:
      base_url: "https://api.example.com"
      auth:
        type: bearer
        token_env: "ORDERS_API_TOKEN"
      allow_write_operations: false
      endpoints:
        - name: get_order
          method: GET
          path: "/orders/{order_id}"
          params:
            - { name: order_id, location: path, type: integer, required: true }
        - name: create_order
          method: POST
          path: "/orders"
          write: true   # stays hidden unless allow_write_operations: true
          params:
            - { name: total, location: body, type: number, required: true }
```

A production-shaped template lives at
[`examples/rest/config.template.yaml`](../examples/rest/config.template.yaml).

> [!NOTE]
> Both a non-`GET` method and an LLM-assisted classification are
> heuristics, not a guarantee the endpoint is actually safe to call
> unsupervised -- review `write:` flags and `allow_write_operations` before
> running against a real backend, same caveat as the SOAP adapter's
> write-verb heuristic (see [security.md](security.md)).

## Configuration reference

| Key | Default | Meaning |
|---|---|---|
| `server.name` | `legacy2mcp` | MCP server name reported to clients |
| `server.transport` | `stdio` | `stdio`, or `http` for Streamable HTTP (see [HTTP transport](#http-transport)) |
| `server.host` | `127.0.0.1` | `http` only: the address to listen on; any non-loopback address needs `server.token_env` |
| `server.port` | `8000` | `http` only |
| `server.path` | `/mcp` | `http` only: the MCP endpoint's path |
| `server.token_env` | — | `http` only: the environment variable holding the bearer token callers must send |
| `adapters[].id` | — | prefix for this adapter's tool names |
| `adapters[].type` | — | `soap`, `db`, or `rest` (implemented); `queue` is on the roadmap |

**SOAP adapter config:**

| Key | Default | Meaning |
|---|---|---|
| `adapters[].config.wsdl_url` | — | WSDL location (`http(s)://…?wsdl` or a file path) |
| `adapters[].config.auth` | `{type: none}` | `none`, `{type: basic, username, password_env}`, or `{type: oauth2, file: "auth.json"}` |
| `adapters[].config.allow_write_operations` | `false` | expose write-like operations |
| `adapters[].config.include_operations` | all | allowlist of operation names |
| `adapters[].config.exclude_operations` | `[]` | denylist of operation names |
| `adapters[].config.timeout_seconds` | `15` | per-call SOAP timeout |

**DB adapter config:**

| Key | Default | Meaning |
|---|---|---|
| `adapters[].config.dialect` | — | `postgres`, `mssql`, or `oracle` |
| `adapters[].config.dsn_env` | — | env var holding the connection string/DSN |
| `adapters[].config.allow_write_operations` | `false` | expose `insert_<table>` and `write: true` queries |
| `adapters[].config.timeout_seconds` | `15` | connection timeout |
| `adapters[].config.tables[].name` / `.schema` | — | table (and optional schema/owner) to allowlist |
| `adapters[].config.tables[].operations` | `[select]` | `select` and/or `insert` |
| `adapters[].config.tables[].columns` | all introspected | column allowlist |
| `adapters[].config.tables[].filterable_columns` | `[]` | columns usable as equality filters on `select_<table>` |
| `adapters[].config.tables[].max_rows` | `200` | row cap, enforced after the query runs |
| `adapters[].config.queries[].name` / `.sql` / `.params` | — | a named query; `sql` uses `:param_name` placeholders |
| `adapters[].config.queries[].write` | `false` | gates this query behind `allow_write_operations` |

**REST adapter config:**

| Key | Default | Meaning |
|---|---|---|
| `adapters[].config.base_url` | — | base URL the configured endpoints are relative to |
| `adapters[].config.auth` | `{type: none}` | `none`, `{type: basic, username, password_env}`, or `{type: bearer, token_env}` |
| `adapters[].config.allow_write_operations` | `false` | expose endpoints marked `write: true` |
| `adapters[].config.timeout_seconds` | `15` | per-call HTTP timeout |
| `adapters[].config.endpoints[].name` / `.method` / `.path` / `.description` | — | one allowlisted endpoint; `path` uses `{param}` placeholders for path params |
| `adapters[].config.endpoints[].params[].name` / `.location` / `.type` / `.required` | — | `location` is `path`, `query`, or `body` |
| `adapters[].config.endpoints[].params[].enum` | unset | a closed set of allowed values, enforced by schema validation — not just descriptive |
| `adapters[].config.endpoints[].write` | `false` | gates this endpoint behind `allow_write_operations` |
| `adapters[].config.endpoints[].confidence` / `.error_codes` / `.needs_review` | unset / `[]` / `false` | only ever populated by `legacy2mcp discover` + `init --from-contract` — how much to trust the finding, known error responses, and whether a human should look at it |

**Security config:**

| Key | Default | Meaning |
|---|---|---|
| `security.audit.enabled` | `true` | write the audit log |
| `security.audit.path` | `./legacy2mcp-audit.log` | audit log location |

## HTTP transport

`legacy2mcp run` speaks MCP over stdio by default, the transport a client
uses when it launches the server itself. For a client that connects over
HTTP instead (a hosted agent, or anything that can't start a subprocess),
set `server.transport: http`:

```yaml
server:
  name: orders
  transport: http
  port: 8765
  token_env: LEGACY2MCP_HTTP_TOKEN # optional on localhost, required beyond it
```

```bash
export LEGACY2MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
legacy2mcp run --config config.yaml   # Serving MCP over HTTP at http://127.0.0.1:8765/mcp
```

The server speaks the MCP SDK's Streamable HTTP transport at
`http://<host>:<port><path>`, with the same tools, validation, audit log
and writes-off defaults as over stdio. A client sends
`Authorization: Bearer <token>` on every request; without it, or with
another token, the answer is `401`. See
[docs/security.md](security.md#the-http-transport) for what is checked.

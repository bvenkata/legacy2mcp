# Adding an adapter for a legacy technology

An **adapter** lets a generated MCP server talk to one kind of backend. legacy2mcp
ships three: SOAP/WSDL, databases (Postgres, SQL Server, Oracle) and REST. If your
organisation runs something else (a mainframe transaction, an IBM i program, an
LDAP directory, files on an SFTP drop, a message queue), an adapter is how it
becomes a safe set of MCP tools.

You only write the part that knows your technology. The server core already does
tool registration, JSON Schema validation, the `describe_capabilities` tool,
error handling and audit logging for every adapter.

Before you start, open a
[New legacy technology](https://github.com/OWASP/legacy2mcp/issues/new?template=new-legacy-technology.yml)
issue (or comment on an existing one) so nobody duplicates the work and a
maintainer can agree the config shape with you.

## How an adapter fits in

```text
config.yaml ──► AdapterConfig (config.py) ──► build_adapter() (server.py)
                                                   │
                                                   ▼
                                    YourAdapter(BaseAdapter)
                                      discover_tools() ──► ToolDefinition list
                                      invoke(name, args) ──► JSON-serializable result
                                                   │
          server.py: validate args ◄───────────────┘──► audit log, clean errors
```

The contract is in [`adapters/base.py`](../src/legacy2mcp/adapters/base.py):

| Method | What it must do |
|---|---|
| `discover_tools()` | Return one `ToolDefinition` per operation: a `name`, a `description`, and an `input_schema` (real JSON Schema with `additionalProperties: false`). Leave write-like operations out unless the config opts in. |
| `invoke(tool_name, arguments)` | Called with arguments the server has **already validated** against your schema. Call the backend, return plain JSON-serializable data. Raise `AdapterError` on failure. |
| `close()` | Optional: release connections. |

## Step by step

### 1. Add a settings model

In [`config.py`](../src/legacy2mcp/config.py), add a Pydantic model for your
adapter's `config:` block, next to `SoapAdapterSettings`, `DbAdapterSettings`
and `RestAdapterSettings`. Then:

- add your type name to `AdapterConfig.type`'s `Literal[...]`;
- add an accessor like `rest_settings()` that validates `self.config` into your model.

Rules for the settings model:

- `allow_write_operations: bool = False`. Always present, always defaulting to false.
- Secrets are **environment-variable names**, never values (`password_env`,
  `token_env`, `dsn_env`). The YAML must stay safe to commit.
- An explicit allowlist of what to expose (operations, tables, programs, paths),
  not "everything".
- `timeout_seconds` with a sensible default.

### 2. Write the adapter

Create `src/legacy2mcp/adapters/<name>_adapter.py`. A minimal read-only example
for a hypothetical "export files on disk" backend:

```python
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from legacy2mcp.adapters.base import AdapterError, BaseAdapter, ToolDefinition
from legacy2mcp.config import CsvAdapterSettings  # your model from step 1


class CsvAdapter(BaseAdapter):
    adapter_type = "csv"

    def __init__(self, adapter_id: str, settings: CsvAdapterSettings):
        super().__init__(adapter_id, settings.model_dump())
        self.settings = settings
        self._files: dict[str, Path] = {}

    def discover_tools(self) -> list[ToolDefinition]:
        tools = []
        for entry in self.settings.files:  # explicit allowlist from config
            name = f"{self.adapter_id}_read_{entry.name}"
            self._files[name] = Path(entry.path)
            tools.append(ToolDefinition(
                name=name,
                description=f"Read up to `limit` rows from {entry.name}.",
                input_schema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": entry.max_rows}},
                    "additionalProperties": False,
                },
                metadata={"adapter_type": self.adapter_type, "side_effect": "read"},
            ))
        return tools

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        path = self._files.get(tool_name)
        if path is None:
            raise AdapterError(f"Unknown tool '{tool_name}'.")
        try:
            with path.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as exc:
            raise AdapterError(f"Could not read {path.name}: {exc}") from exc
        return rows[: arguments.get("limit", 50)]
```

Tool names are `<adapter_id>_<operation>`. Put provenance in `metadata`
(`side_effect`, `confidence`, `error_codes`): `describe_capabilities` and
`legacy2mcp inspect` show it.

### 3. Register it

In [`server.py`](../src/legacy2mcp/server.py), add a branch to `build_adapter()`:

```python
if adapter_config.type == "csv":
    return CsvAdapter(adapter_config.id, adapter_config.csv_settings())
```

If the backend needs a third-party client library, import it **inside** the
adapter (lazily, like the database drivers) and add an optional extra in
`pyproject.toml` (for example `legacy2mcp[csv]`), so the core install stays small.

### 4. Test it without the real system

Tests must run offline and in CI, with no real mainframe or vendor service. Follow
[`tests/test_rest_adapter.py`](../tests/test_rest_adapter.py) and
[`tests/mock_soap_service.py`](../tests/mock_soap_service.py): stand up an
in-process fake (or a temp directory, or SQLite) and cover at least:

- `discover_tools()` returns the expected names and schemas;
- write operations are **absent** by default and present only with `allow_write_operations: true`;
- `invoke()` returns plain JSON for a good call;
- a backend failure surfaces as `AdapterError`, not a stack trace;
- an end-to-end call through the server is audit-logged (see
  [`tests/test_end_to_end.py`](../tests/test_end_to_end.py)).

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

### 5. Document it

- A section in [`docs/configuration.md`](configuration.md): a `config.yaml`
  example and a table of every key.
- A commented template under `examples/<name>/config.template.yaml`.
- Anything security-relevant (what's a write, what isn't covered) in
  [`docs/security.md`](security.md).
- A line in [`CHANGELOG.md`](../CHANGELOG.md) under the unreleased version.

## Safety checklist

A maintainer will check every adapter PR against this list:

- [ ] Read-only by default; writes need `allow_write_operations: true`.
- [ ] Only explicitly allowlisted operations are exposed.
- [ ] Every input schema is real JSON Schema with `additionalProperties: false`,
      derived from the live system wherever the technology allows.
- [ ] No way for the model to send raw commands, SQL, shell or query text.
- [ ] Secrets come from environment variables, never the config file or logs.
- [ ] Failures raise `AdapterError` with a clean message.
- [ ] Network calls go only to the endpoints in the config.
- [ ] Tests run offline in CI.

## Getting help

Ask in the issue for your technology, in
[GitHub Discussions](https://github.com/OWASP/legacy2mcp/discussions), or on OWASP
Slack in [`#project-legacy2mcp`](https://owasp.org/slack/invite). Draft PRs are
welcome early, especially to agree the config shape before you write the tests.

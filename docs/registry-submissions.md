# Registry and awesome-list submissions

Maintainer checklist for getting legacy2mcp in front of people who browse MCP directories.
Copy-paste text is below. Check each list's own contribution rules before opening a PR, because
formats change.

## Status

| Where | State | Notes |
|---|---|---|
| Official MCP Registry | Published (`io.github.bvenkata/legacy2mcp`) | Automatic on tagged release via `release.yml`; see [releasing.md](releasing.md). |
| PyPI | Published | `pip install legacy2mcp` |
| Glama | Listed (`glama.json` present, badge in README) | Claim and complete the listing on glama.ai so the score badge stays accurate. |
| PulseMCP and other aggregators | Usually ingest the official registry | Search for the listing a few days after a release; claim it if it exists. |
| awesome-mcp-servers lists | Not submitted | Entries below. One PR per list. |
| Smithery, mcp.so | Not submitted | Manual submission forms. Reuse the text below. |

## Reusable text

**One-liner (under 100 characters):**

> Turn a legacy SOAP/WSDL, database, or REST API into a safe, typed, audit-logged MCP server

**Short description (awesome lists):**

> Point it at a WSDL, database, or REST API and get an MCP server with schema-validated inputs, read-only by default, and an audit log of every call.

**Longer description (directories with a text box):**

> legacy2mcp generates an MCP server from a SOAP/WSDL service, a database (Postgres, SQL Server,
> Oracle), or a REST API. Tool schemas come from the source (WSDL types, live DB columns, endpoint
> config), so they cannot drift. Every call is validated against its schema before it reaches the
> backend, write operations are hidden unless you opt in, the LLM never writes SQL, and every call
> is audit-logged. `legacy2mcp init` can scaffold a config from a WSDL URL, an OpenAPI/Swagger
> URL, or a repo. Python, Apache 2.0, stdio transport.

**Install and run:**

```
uvx legacy2mcp run --config config.yaml
```

**Tags:** mcp, soap, wsdl, database, rest, legacy, enterprise, python, audit, read-only

## awesome-mcp-servers entries

Match the live README's exact format (emoji legend, link style, alphabetical order). Draft:

```markdown
- [bvenkata/legacy2mcp](https://github.com/bvenkata/legacy2mcp) - Turn a legacy SOAP/WSDL service, database, or REST API into a safe, typed, audit-logged MCP server. Schema-validated inputs, read-only by default.
```

Likely categories: a developer-tools or "other tools and integrations" section, or a databases
section if the list has one. Lists to target (verify the current repo and rules first):

- punkpeek/awesome-mcp-servers
- wong2/awesome-mcp-servers (submits via mcpservers.org)
- appcypher/awesome-mcp-servers

PR title: `Add legacy2mcp`. In the PR body, link the demo configs so reviewers can try it in under a
minute: `examples/rest/config.petstore.yaml` and `examples/soap/config.orders.yaml`.

## Before you submit

- [ ] README renders on PyPI (images use absolute URLs; the `mcp-name` marker is present).
- [ ] The newest version is tagged, so PyPI and the registry match `pyproject.toml` and `server.json`.
- [ ] A GitHub Release exists with notes.
- [ ] `legacy2mcp init --rest-url https://petstore3.swagger.io/api/v3` works on a clean machine.
- [ ] Repo topics include `mcp`, `mcp-server`, `model-context-protocol`.

# Examples

Prefer to watch it instead of reading? [`../demo/legacy2mcp.gif`](../demo/legacy2mcp.gif)
is a terminal recording of `legacy2mcp init` against the REST demo below.

## SOAP / WSDL

| File | What it shows |
|------|---------------|
| [`soap/config.calculator.yaml`](soap/config.calculator.yaml) | Minimal config against the bundled demo Calculator service (localhost) |
| [`soap/config.calculator.docker.yaml`](soap/config.calculator.docker.yaml) | Same, but wired for `docker compose` service names |
| [`soap/config.template.yaml`](soap/config.template.yaml) | **Production-shaped template**: a legacy SOAP service with basic auth, an explicit operation allowlist, and audit logging |
| [`soap/run_mock_calculator.py`](soap/run_mock_calculator.py) | Starts the in-process demo SOAP service on `http://127.0.0.1:8123/calculator` |
| [`soap/orders.wsdl`](soap/orders.wsdl) + [`soap/mock_orders_service.py`](soap/mock_orders_service.py) | Sample "Orders" service with read (`GetOrder`, `ListOrders`, `GetInventory`) and write (`CreateOrder`, `CancelOrder`) operations, for seeing the write-gate in action |
| [`soap/config.orders.yaml`](soap/config.orders.yaml) | Points at the local mock Orders service above |
| [`soap/config.orders.public-wsdl.yaml`](soap/config.orders.public-wsdl.yaml) | Points at `orders.wsdl` by its raw GitHub URL -- `inspect` with no cloning (can't call tools, since the WSDL's bound address is local) |

### Try the demo end to end (no external network)

```bash
pip install -e ".[dev]"

# terminal 1 - start the demo SOAP service
python examples/soap/run_mock_calculator.py

# terminal 2 - discover the tools generated from its WSDL
legacy2mcp inspect --config examples/soap/config.calculator.yaml
```

Or with Docker:

```bash
docker compose up demo-soap-service -d
docker compose run --rm legacy2mcp legacy2mcp inspect \
  --config examples/soap/config.calculator.docker.yaml
```

### Try the WSDL-by-URL variant (no cloning)

`inspect` only needs the WSDL itself, so you can point it at this repo's raw
GitHub URL instead of cloning. It can list tools but not call them, since
the WSDL's bound address is local:

```bash
curl -O https://raw.githubusercontent.com/bvenkata/legacy2mcp/main/examples/soap/config.orders.public-wsdl.yaml
legacy2mcp inspect --config config.orders.public-wsdl.yaml
```

### Point it at your own WSDL

1. Copy `soap/config.template.yaml` to `config.yaml`.
2. Set `wsdl_url`, `auth`, and the operation allow/deny lists.
3. `export <YOUR_PASSWORD_ENV>=...`
4. `legacy2mcp inspect --config config.yaml` to review the generated tools.
5. `legacy2mcp run --config config.yaml` to start the MCP server, or wire it
   into an MCP client (see the root README).

## REST / JSON APIs

| File | What it shows |
|------|---------------|
| [`rest/config.template.yaml`](rest/config.template.yaml) | **Production-shaped template**: a REST API with bearer auth, an explicit endpoint allowlist, and audit logging |
| [`rest/config.petstore.yaml`](rest/config.petstore.yaml) | Zero-setup demo against the public Swagger Petstore -- no API key, nothing to run locally |

There's no live schema to introspect a plain REST API from the way there is
a WSDL or a DB catalog, so either write `rest/config.template.yaml` by hand,
or let `legacy2mcp init` generate a starting point. It asks what to point at
-- a WSDL URL, a REST API URL (tries OpenAPI/Swagger discovery against it),
a website with no API docs (`--webpage-url` crawls it), or a GitHub
repo/local path (scans Flask/FastAPI route decorators) -- either
interactively or via a flag:

```bash
legacy2mcp init --rest-url https://api.example.com --out config.yaml
# or: --wsdl-url ... / --webpage-url ... / --github-url ... --scan-only (skip AI assist)
# review config.yaml -- especially base_url, auth, and any TODOs it left
legacy2mcp inspect --config config.yaml
```

See [the root README](../README.md#generate-a-config-automatically) for the
full `legacy2mcp init` flags.

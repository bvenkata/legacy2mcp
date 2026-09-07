# Examples

## SOAP / WSDL

| File | What it shows |
|------|---------------|
| [`soap/config.calculator.yaml`](soap/config.calculator.yaml) | Minimal config against the bundled demo Calculator service (localhost) |
| [`soap/config.calculator.docker.yaml`](soap/config.calculator.docker.yaml) | Same, but wired for `docker compose` service names |
| [`soap/config.claims.template.yaml`](soap/config.claims.template.yaml) | **Production-shaped template**: a legacy insurance claims platform with basic auth, an explicit operation allowlist, and audit logging |
| [`soap/run_mock_calculator.py`](soap/run_mock_calculator.py) | Starts the in-process demo SOAP service on `http://127.0.0.1:8123/calculator` |

### Try the demo end to end (no external network)

```bash
pip install -e ".[dev]"

# terminal 1 - start the demo SOAP service
python examples/soap/run_mock_calculator.py

# terminal 2 - discover the tools generated from its WSDL
legacy2mcp inspect --config examples/soap/config.calculator.yaml
```

### Point it at your own WSDL

1. Copy `soap/config.claims.template.yaml` to `config.yaml`.
2. Set `wsdl_url`, `auth`, and the operation allow/deny lists.
3. `export <YOUR_PASSWORD_ENV>=...`
4. `legacy2mcp inspect --config config.yaml` to review the generated tools.
5. `legacy2mcp run --config config.yaml` to start the MCP server, or wire it
   into an MCP client (see the root README).

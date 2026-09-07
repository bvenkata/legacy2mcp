#!/usr/bin/env bash
# Runs the legacy2mcp demo end to end against the bundled mock SOAP service.
# Used two ways:
#   - on its own:   ./demo/demo.sh
#   - as the script VHS records: `vhs demo/demo.tape` (see demo/README.md)
#
# Run it from the repo root. Expects the package importable (either
# `pip install -e ".[dev]"` or the local .venv this repo builds).
set -euo pipefail

BIN="legacy2mcp"
PY="python"
if [ -x ".venv/bin/legacy2mcp" ]; then BIN=".venv/bin/legacy2mcp"; PY=".venv/bin/python"; fi

CONFIG="examples/soap/config.calculator.yaml"

# 1. Start the bundled demo SOAP service (dneonline-style Calculator WSDL).
"$PY" examples/soap/run_mock_calculator.py >/dev/null 2>&1 &
MOCK_PID=$!
trap 'kill "$MOCK_PID" 2>/dev/null || true' EXIT
sleep 3

set -x

# 2. The entire config: one WSDL URL, no adapter code.
cat "$CONFIG"

# 3. Introspect the WSDL -> one typed MCP tool per operation.
"$BIN" inspect --config "$CONFIG" | jq '[.[] | {name, required: .input_schema.required}]'

# 4. The schemas are real, built from the WSDL's own XSD types.
"$BIN" inspect --config "$CONFIG" | jq '.[0].input_schema'

set +x
echo
echo "4 WSDL operations -> 4 schema-validated, audit-logged MCP tools. No adapter code."

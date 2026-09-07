# legacy2mcp demo recording - driven by VHS (https://github.com/charmbracelet/vhs)
#
#   brew install vhs
#   vhs demo/demo.tape        # run from the repo root
#
# Produces demo/legacy2mcp.gif and demo/legacy2mcp.mp4.
# Keep every Type line ASCII-only: VHS mistypes non-ASCII characters.

Output demo/legacy2mcp.gif
Output demo/legacy2mcp.mp4

Set Shell "bash"
Set FontSize 16
Set Width 1280
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms
Set Margin 0

# --- setup (hidden): prompt, PATH, start the bundled mock SOAP service ---
Hide
Type "export PS1='$ '"
Enter
Type "[ -d .venv ] && export PATH=$PWD/.venv/bin:$PATH"
Enter
Type "python examples/soap/run_mock_calculator.py >/dev/null 2>&1 &"
Enter
Type "sleep 3 && clear"
Enter
Sleep 3s
Show

Type "# legacy2mcp: turn a legacy SOAP/WSDL service into safe, typed MCP tools"
Sleep 800ms
Enter
Sleep 1s

Type "# 1. The whole config - just a WSDL URL. No adapter code."
Enter
Sleep 400ms
Type "cat examples/soap/config.calculator.yaml"
Enter
Sleep 3s

Type "# 2. Introspect the WSDL -> one typed MCP tool per operation"
Enter
Sleep 400ms
Type "legacy2mcp inspect --config examples/soap/config.calculator.yaml | jq 'map(.name)'"
Enter
Sleep 3s

Type "# 3. Each tool carries a real JSON Schema, built from the WSDL's XSD types"
Enter
Sleep 400ms
Type "legacy2mcp inspect --config examples/soap/config.calculator.yaml | jq '.[0]'"
Enter
Sleep 3500ms

Type "# Every call is schema-validated before it hits SOAP."
Enter
Type "# Writes are off by default. Every call is audit-logged."
Enter
Sleep 2500ms

Hide
Type "kill %1 2>/dev/null; true"
Enter
Show
Sleep 500ms

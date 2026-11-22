<!-- Run this skill with /legacy2mcp in any Claude Code session -->

legacy2mcp turns an existing SOAP/WSDL service, a database, a REST API, or a
plain website with no API docs into a safe, typed, audit-logged MCP server
-- read-only by default, every call audit-logged, no hand-written adapter
code or hand-maintained schemas.

Common commands:

- Point at a live WSDL (no API key needed; zeep introspects it live):
    legacy2mcp init --wsdl-url "https://service.example.com/Legacy?wsdl" --out config.yaml
- Point at a live REST API (tries OpenAPI/Swagger discovery, no API key needed):
    legacy2mcp init --rest-url "https://api.example.com" --out config.yaml
- Point at a website with no API docs at all (crawls it for forms/content;
  every operation lands confidence=low/needs_review=true -- review before trusting):
    legacy2mcp init --webpage-url "https://your-legacy-app.example.com/some-page" --out config.yaml
- Scan a GitHub repo or local codebase for a SOAP client / ORM model / routes:
    legacy2mcp init --github-url https://github.com/you/your-api --scan-only --out config.yaml
    legacy2mcp init --local-path ./path/to/repo --scan-only --out config.yaml
- See the real discovered tools before trusting anything (no server started):
    legacy2mcp inspect --config config.yaml
- Start the MCP server (an MCP client normally launches this itself --
  running it by hand just blocks waiting for a client over stdio):
    legacy2mcp run --config config.yaml

After `init`, review config.yaml's `# TODO` comments and the plain-English
config.html report before trusting the generated config -- it's a static/
introspected best effort, not yet verified against the live backend.
Write-like operations stay hidden unless `allow_write_operations: true` is
set explicitly, and every tool call is audit-logged to
./legacy2mcp-audit.log. Database access is an explicit table/column
allowlist generated from the live schema -- never arbitrary SQL.

To wire the generated server into an MCP client, add to its config (use an
absolute path to config.yaml):
    {"mcpServers": {"<name>": {"command": "legacy2mcp", "args": ["run", "--config", "/absolute/path/to/config.yaml"]}}}

Full docs: https://github.com/OWASP/legacy2mcp


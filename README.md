<!-- mcp-name: io.github.OWASP/legacy2mcp -->

<p align="center">
  <a href="https://owasp.org/projects/legacy2mcp">
    <img src="assets/banner.svg" alt="OWASP legacy2mcp: legacy SOAP/WSDL, database, REST API, or website turned into safe, typed MCP tools" width="100%" />
  </a>
</p>

<div align="center">

# OWASP legacy2mcp

**Turn legacy systems into safe, typed, audit-logged MCP servers.**<br/>
SOAP/WSDL services, databases, REST APIs, websites with no API docs, and whole Python or JS/TS codebases.

[![CI](https://github.com/OWASP/legacy2mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/OWASP/legacy2mcp/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/legacy2mcp.svg)](https://pypi.org/project/legacy2mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/legacy2mcp.svg)](https://pypi.org/project/legacy2mcp/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![OWASP Incubator](https://img.shields.io/badge/OWASP-Incubator-48A646.svg?logo=owasp)](https://owasp.org/projects/legacy2mcp)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.OWASP%2Flegacy2mcp-6f42c1)](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.OWASP/legacy2mcp)

</div>

## Why legacy2mcp

- **No adapter code.** Tool schemas are built from the live WSDL, database
  schema or OpenAPI spec, so they can't drift from the real system.
- **Read-only by default.** Write operations stay hidden until you set
  `allow_write_operations: true`.
- **Every call is checked and logged.** Arguments are schema-validated before
  they reach your backend, and each call is written to an audit log.
- **No arbitrary SQL.** Database access is an explicit table and column allowlist.
- **Runs locally.** No API key, no telemetry.

## Quick start

```bash
pip install legacy2mcp

legacy2mcp init --rest-url "https://petstore3.swagger.io/api/v3"            # REST API
legacy2mcp init --wsdl-url "http://www.dneonline.com/calculator.asmx?WSDL"  # SOAP service
legacy2mcp init --webpage-url "https://legacy2mcp.com/"                     # website, no API docs
```

`init` writes `config.yaml` and a plain-English `config.html` report, opens a
local test-chat page to try the tools, and prints a snippet to paste into your
MCP client. Review the `# TODO` comments in `config.yaml`, then check the tools:

```bash
legacy2mcp inspect --config config.yaml
```

Add the server to your MCP client (Claude Desktop, Cursor, VS Code, Claude Code):

```json
{
  "mcpServers": {
    "legacy2mcp": {
      "command": "legacy2mcp",
      "args": ["run", "--config", "/absolute/path/to/config.yaml"]
    }
  }
}
```

Per-client setup: [docs/clients.md](docs/clients.md).

## Analyze a whole codebase

Point legacy2mcp at a local Python or JavaScript/TypeScript repo. It traces
every business flow, labels each one read-only, low-risk write or high-risk,
and proposes MCP tools, an agent setup and a roadmap, all in one offline
report. Nothing is executed or uploaded.

```bash
pip install "legacy2mcp[analyze]"
legacy2mcp analyze ./my-app --no-ai          # writes legacy2mcp-analysis/report.html
legacy2mcp generate ./legacy2mcp-analysis --selections selections.json \
  --base-url https://staging.my-app.example  # config for the flows you approved
```

<p align="center">
  <img src="assets/report-summary.png" alt="legacy2mcp report: executive summary with KPIs, recommended first steps and flow-risk breakdown" width="820">
</p>

See the [sample report](docs/sample-report/) and the [analyze guide](docs/analyze.md).

## What it connects to

| Source | Command | Details |
|---|---|---|
| SOAP / WSDL service | `init --wsdl-url` | [configuration](docs/configuration.md#point-it-at-your-own-wsdl) |
| Database (Postgres, SQL Server, Oracle) | hand-written config | [configuration](docs/configuration.md#point-it-at-your-own-database) |
| REST API | `init --rest-url` | [configuration](docs/configuration.md#point-it-at-your-own-rest-api) |
| Website with no API docs | `init --webpage-url` | [CLI guide](docs/cli.md#generate-a-config-automatically) |
| GitHub repo or local source | `init --github-url` / `--local-path` | [CLI guide](docs/cli.md#generate-a-config-automatically) |
| Postman, OpenAPI file, HAR, stored procedures, queues | `discover` | [CLI guide](docs/cli.md#discover-a-legacy-interface-before-generating-a-server) |
| Whole Python or JS/TS codebase | `analyze` + `generate` | [analyze guide](docs/analyze.md) |

> [!WARNING]
> Write detection for SOAP operations is a name heuristic. For any system where
> a wrong call matters, list operations explicitly with `include_operations`.
> The MCP server has no authentication of its own yet, so don't expose it to
> untrusted callers. See [docs/security.md](docs/security.md).

## Documentation

- [CLI guide](docs/cli.md): `init`, `discover`, `demo`, `inspect`, CI usage
- [Configuration](docs/configuration.md): adapter examples and every config key
- [How it works](docs/how-it-works.md): architecture, coverage, use cases
- [Analyze guide](docs/analyze.md): codebase analysis and `generate`
- [Clients](docs/clients.md): connecting each MCP client
- [Security model](docs/security.md): what's protected, data flow, known limits
- [Roadmap](docs/roadmap.md) · [Examples](examples/README.md) · [Changelog](CHANGELOG.md)

## Community and contributing

- **OWASP Slack:** [`#project-legacy2mcp`](https://owasp.org/slack/invite)
- **Issues:** https://github.com/OWASP/legacy2mcp/issues ·
  good starting points are labeled [`good first issue`](https://github.com/OWASP/legacy2mcp/labels/good%20first%20issue)
- **Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md)
- **Security issues:** report privately per the [security policy](SECURITY.md)

## Authors & maintainers

- **[Balaji Venkatasubramaniyar](https://github.com/bvenkata)**: Project Creator and Lead Maintainer

See [AUTHORS](AUTHORS) and [MAINTAINERS.md](MAINTAINERS.md).

## How to cite

Use GitHub's "Cite this repository" button ([CITATION.cff](CITATION.cff)), or:

```bibtex
@software{legacy2mcp,
  author  = {Venkatasubramaniyar, Balaji},
  title   = {OWASP legacy2mcp: Safe, Typed, Audit-Logged MCP Servers for Legacy Systems},
  url     = {https://github.com/OWASP/legacy2mcp},
  license = {Apache-2.0}
}
```

## License

Apache 2.0. See [LICENSE](LICENSE).

<div align="center"><sub>OWASP legacy2mcp · an <a href="https://owasp.org/projects/legacy2mcp">OWASP Foundation</a> project</sub></div>

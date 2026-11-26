<!-- mcp-name: io.github.OWASP/legacy2mcp -->

<p align="center">
  <a href="https://owasp.org/projects/legacy2mcp">
    <img src="assets/banner.svg" alt="OWASP legacy2mcp: analyze a codebase, label every business flow by risk, and turn the approved flows into safe MCP tools" width="100%" />
  </a>
</p>

<div align="center">

# OWASP legacy2mcp

**Understand any codebase, then turn the flows you approve into a safe MCP server.**<br/>
Python and JavaScript/TypeScript. Offline, no API key, nothing executed or uploaded.

[![CI](https://github.com/OWASP/legacy2mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/OWASP/legacy2mcp/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/legacy2mcp.svg)](https://pypi.org/project/legacy2mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/legacy2mcp.svg)](https://pypi.org/project/legacy2mcp/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![OWASP Incubator](https://img.shields.io/badge/OWASP-Incubator-48A646.svg?logo=owasp)](https://owasp.org/projects/legacy2mcp)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.OWASP%2Flegacy2mcp-6f42c1)](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.OWASP/legacy2mcp)

</div>

## Quick start

```bash
pip install "legacy2mcp[analyze]"
legacy2mcp analyze ./my-app --no-ai          # writes legacy2mcp-analysis/report.html
legacy2mcp generate ./legacy2mcp-analysis --selections selections.json \
  --base-url https://staging.my-app.example  # config for the flows you approved
```

<p align="center">
  <img src="assets/report-summary.png" alt="legacy2mcp report: executive summary with KPIs, recommended first steps and flow-risk breakdown" width="820">
</p>

See a [sample report from a real open-source app](docs/sample-report/).

## How it works

1. **Analyze.** `legacy2mcp analyze` maps the codebase: entry points (HTTP
   routes, CLI commands, scheduled jobs, queue consumers), what each one
   calls, and every side effect (database writes, outbound HTTP, email,
   payments, queues, auth changes). It traces one business flow per entry
   point and labels it **read-only**, **low-risk write** or **high-risk**,
   with the `file:line` behind each reason.
2. **Review.** Open `legacy2mcp-analysis/report.html`. It shows the flows and
   their risk, the proposed MCP tools, an agent setup with approval points on
   every write, and a phased roadmap. Keep the tools you want (writes start
   unticked) and click **Export selections.json**.
3. **Generate.** `legacy2mcp generate` turns those selections into
   `config.yaml`. Every tool gets schema validation and audit logging, and
   write tools stay hidden unless you pass `--allow-writes`.
4. **Run.** Check the tools, then add the server to your MCP client:

```bash
legacy2mcp inspect --config config.yaml
```

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

<p align="center">
  <img src="assets/report-topology.png" alt="Proposed agent topology: a coordinator, specialist agents per domain, and approval gates in front of write tools" width="760">
</p>

## Supported frameworks

- **Python:** Flask, FastAPI, Django, click/typer, Celery, APScheduler
- **JavaScript / TypeScript:** Express, Next.js API routes, commander,
  node-cron, BullMQ, amqplib, kafkajs

Full list, AI enrichment (optional, bring your own key) and every flag:
[analyze guide](docs/analyze.md).

## Safe by default

- Nothing in the scanned repo is imported, executed or sent anywhere.
- Generated servers are read-only unless you opt in to writes.
- Every tool call is schema-validated before it reaches your backend and
  written to an audit log.
- AI enrichment is off unless you provide a key; it never changes a risk
  level or which tools are selected.

See the [security model](docs/security.md).

> [!TIP]
> Already have a SOAP service, database, REST API or website instead of a
> codebase? `legacy2mcp init` connects to it directly. See the
> [CLI guide](docs/cli.md).

## Documentation

- [Analyze guide](docs/analyze.md) · [CLI guide](docs/cli.md) ·
  [Configuration](docs/configuration.md) · [How it works](docs/how-it-works.md)
- [Clients](docs/clients.md) · [Security model](docs/security.md) ·
  [Roadmap](docs/roadmap.md) · [Changelog](CHANGELOG.md)

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

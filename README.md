<!-- mcp-name: io.github.OWASP/legacy2mcp -->

<p align="center">
  <a href="https://owasp.org/projects/legacy2mcp">
    <img src="assets/banner-analyze.svg" alt="OWASP legacy2mcp: analyze a codebase, label every business flow by risk, and turn the approved flows into safe MCP tools" width="100%" />
  </a>
</p>

<div align="center">

# OWASP legacy2mcp

**Understand any codebase, then turn the flows you approve into a safe MCP server.**<br/>
Python and JavaScript/TypeScript. Offline, no API key, nothing executed or uploaded.

<p>
  <a href="https://owasp.org/projects/legacy2mcp"><img src="https://img.shields.io/badge/level-incubator-2f80ed?style=for-the-badge" alt="Level: Incubator" /></a>
  <a href="https://owasp.org/projects/legacy2mcp"><img src="https://img.shields.io/badge/type-code-2f80ed?style=for-the-badge" alt="Type: Code" /></a>
  <a href="https://owasp.org/slack/invite"><img src="https://img.shields.io/badge/slack-%23project--legacy2mcp-2f80ed?style=for-the-badge" alt="Slack: #project-legacy2mcp" /></a>
  <a href="https://github.com/OWASP/legacy2mcp/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/OWASP/legacy2mcp/ci.yml?branch=main&label=build&style=for-the-badge" alt="Build status" /></a>
</p>

<p>
  <a href="https://github.com/OWASP/legacy2mcp/blob/main/LICENSE"><img src="https://img.shields.io/github/license/OWASP/legacy2mcp?color=2f80ed&style=for-the-badge" alt="License" /></a>
  <a href="https://github.com/OWASP/legacy2mcp/commits/main"><img src="https://img.shields.io/github/last-commit/OWASP/legacy2mcp?color=2f80ed&style=for-the-badge" alt="Last commit" /></a>
  <a href="https://github.com/OWASP/legacy2mcp/graphs/contributors"><img src="https://img.shields.io/github/contributors/OWASP/legacy2mcp?color=2f80ed&style=for-the-badge" alt="Contributors" /></a>
</p>

<p>
  <a href="https://github.com/OWASP/legacy2mcp/fork"><img src="https://img.shields.io/github/forks/OWASP/legacy2mcp?color=2f80ed&label=forks&style=for-the-badge" alt="Forks" /></a>
  <a href="https://github.com/OWASP/legacy2mcp/stargazers"><img src="https://img.shields.io/github/stars/OWASP/legacy2mcp?color=2f80ed&label=stars&style=for-the-badge" alt="Stars" /></a>
  <a href="https://pepy.tech/project/legacy2mcp"><img src="https://img.shields.io/pepy/dt/legacy2mcp?color=2f80ed&label=downloads&style=for-the-badge" alt="Downloads" /></a>
</p>

<p>
  <a href="https://github.com/OWASP/legacy2mcp/issues"><img src="https://img.shields.io/github/issues/OWASP/legacy2mcp?color=2f80ed&label=issues&style=for-the-badge" alt="Issues" /></a>
  <a href="https://github.com/OWASP/legacy2mcp/pulls"><img src="https://img.shields.io/github/issues-pr/OWASP/legacy2mcp?color=2f80ed&label=pull%20requests&style=for-the-badge" alt="Pull requests" /></a>
</p>

<p>
  <a href="https://pypi.org/project/legacy2mcp/"><img src="https://img.shields.io/pypi/v/legacy2mcp?color=2f80ed&label=pypi&style=for-the-badge" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/legacy2mcp/"><img src="https://img.shields.io/pypi/pyversions/legacy2mcp?color=2f80ed&label=python&style=for-the-badge" alt="Python versions" /></a>
  <a href="https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.OWASP/legacy2mcp"><img src="https://img.shields.io/badge/mcp%20registry-io.github.OWASP%2Flegacy2mcp-2f80ed?style=for-the-badge" alt="MCP Registry" /></a>
</p>

<p>
  <a href="https://github.com/OWASP/legacy2mcp"><img src="https://img.shields.io/badge/created-sep%2C%202026-2f80ed?style=for-the-badge" alt="Created: Sep 2026" /></a>
</p>

</div>

## What is legacy2mcp?

legacy2mcp is an **OWASP Incubator Project** that helps teams give AI agents
safe access to existing applications. It reads a Python or JavaScript/TypeScript
codebase, works out what each part of it actually does, and turns the parts you
approve into an **MCP server with guardrails built in**.

Instead of hand-writing an MCP adapter for every endpoint and hoping nothing
dangerous slips through, legacy2mcp:

- **Maps** every entry point (HTTP routes, CLI commands, scheduled jobs, queue
  consumers) and the business flow behind it.
- **Labels** each flow read-only, low-risk write or high-risk, with the file
  and line behind every reason.
- **Proposes** the MCP tools, an agent setup with approval points, and a
  phased roadmap in one report.
- **Generates** a server for just the flows you approve: read-only by default,
  every call schema-validated and audit-logged.

> [!NOTE]
> **Everything runs locally.** Your code is parsed, never imported or executed,
> and nothing is uploaded. Optional AI enrichment is off unless you provide a
> key; it sends flow structure (source code only if you opt in), with secrets
> redacted, and only after you confirm. See
> [Data flow and privacy](docs/security.md#data-flow-and-privacy) and the
> [analyze guide](docs/analyze.md).

## How it works

```mermaid
flowchart LR
  A["Your codebase<br/>Python · JS/TS"] --> B["analyze<br/>entry points, flows,<br/>side effects"]
  B --> C{"Risk per flow"}
  C -->|read-only| D["report.html<br/>you tick the tools"]
  C -->|low-risk write| D
  C -->|high-risk| D
  D -->|selections.json| E["generate<br/>config.yaml"]
  E --> F["MCP server<br/>schema-validated,<br/>audit-logged"]
  F --> G["AI agent /<br/>MCP client"]
```

1. **Analyze**: parse the code and trace one business flow per entry point.
2. **Review**: read the risk of each flow in `report.html` and tick the tools to expose.
3. **Generate**: write `config.yaml` for the approved tools; writes stay hidden unless you opt in.
4. **Connect**: your MCP client launches the server, which validates and logs every call.

## Quick start

You need Python 3.10+ and a local Python or JavaScript/TypeScript project.
No API key. Nothing in your project is run or uploaded.

**1. Install**

```bash
pip install "legacy2mcp[analyze]"
```

**2. Analyze your project**

```bash
legacy2mcp analyze ./my-app --no-ai
```

This creates a `legacy2mcp-analysis/` folder with `report.html` in it.

**3. Review the report**

Open `legacy2mcp-analysis/report.html` in your browser. Every business flow is
labelled **read-only**, **low-risk write** or **high-risk**, with the file and
line behind each label. Under **Proposed MCP tools**, tick the tools you want
(read-only tools are ticked for you) and click **Export selections.json**.

**4. Generate the server config**

```bash
legacy2mcp generate ./legacy2mcp-analysis \
  --selections ~/Downloads/selections.json \
  --base-url https://staging.my-app.example
```

- `--selections`: the file you exported in step 3. Leave it out to use the
  read-only tools only.
- `--base-url`: where your app is running, so the tools can call it.

This writes `config.yaml`. Write tools stay hidden unless you add `--allow-writes`.

**5. Connect it to your AI client**

Check the tools, then add this to your MCP client's config (Claude Desktop,
Cursor, VS Code, Claude Code):

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

Per-client setup: [docs/clients.md](docs/clients.md).

## What the report shows

<p align="center">
  <img src="assets/report-summary.png" alt="legacy2mcp report: executive summary with KPIs, recommended first steps and flow-risk breakdown" width="820">
</p>

- Every entry point (HTTP routes, CLI commands, scheduled jobs, queue
  consumers), what it calls, and its side effects: database writes, outbound
  HTTP, email, payments, queues, auth changes.
- A risk label and reasons for every flow, linked to the source line.
- The proposed MCP tools, an agent setup with approval points on every
  write, and a phased roadmap.

<p align="center">
  <img src="assets/report-topology.png" alt="Proposed agent topology: a coordinator, specialist agents per domain, and approval gates in front of write tools" width="760">
</p>

See a [sample report from a real open-source app](docs/sample-report/).

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

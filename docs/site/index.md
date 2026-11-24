---
title: "legacy2mcp: understand any codebase, then turn it into safe MCP tools"
description: "Open-source, local-first CLI that maps a Python or JavaScript/TypeScript codebase, traces its business flows, labels their risk, and generates read-only-by-default, audit-logged MCP servers for AI agents."
canonical: https://legacy2mcp.com/
---

# Understand any codebase. Then decide what AI agents may touch.

**legacy2mcp** is an open-source command-line tool (Apache 2.0, an OWASP
Incubator Project) that reads your existing application, tells you what it
does and how risky each part is, and then generates a
[Model Context Protocol](https://modelcontextprotocol.io) server for exactly
the parts you approve. It runs on your machine, needs no API key, and every
generated tool is read-only by default and audit-logged.

```bash
pip install "legacy2mcp[analyze]"
legacy2mcp analyze ./my-app --no-ai
```

[See a sample report](https://github.com/bvenkata/legacy2mcp/tree/main/docs/sample-report) ·
[Read the guide](https://github.com/bvenkata/legacy2mcp/blob/main/docs/analyze.md) ·
[GitHub](https://github.com/bvenkata/legacy2mcp)

## What you get

- **A map of the code.** Languages, frameworks, modules and their imports, every
  function, and every call into a database, payment provider, email
  service, queue, external API or the filesystem.
- **Every business flow.** One per entry point (HTTP route, CLI command,
  scheduled job or queue consumer), traced step by step through the code,
  including the background work it hands off.
- **A risk label with reasons.** Read-only, low-risk write, or high-risk (moves money,
  deletes data, sends outside the system, changes credentials). Each reason
  points to a file and line. Writes without visible authentication are flagged.
- **Proposed MCP tools.** Names, descriptions and validated input schemas, with
  writes excluded until you opt in.
- **An agent blueprint.** A coordinator, one specialist agent per business
  domain, approval gates in front of every write, and where documents, images
  or voice could feed in.
- **A phased roadmap.** Read-only tools first, low-risk writes with approval
  next, full orchestration last, with effort and risk for each phase.
- **A generated server.** `legacy2mcp generate` turns your selections into a config
  for legacy2mcp's MCP runtime: schema validation on every call, an audit
  log, and write tools hidden unless enabled.

All of it lands in one offline HTML report you can open from disk, print to
PDF, or send to a colleague.

## How it works

1. **Local static analysis.** Python is parsed with the standard library's `ast`
   module, JavaScript and TypeScript with tree-sitter. Nothing in your repo is
   imported, installed or executed, and nothing is uploaded.
2. **Graphs and flows.** It builds call, import and data-access graphs, then traces a
   flow from every entry point.
3. **Rules first.** Risk comes from deterministic rules over the side effects
   found, so the same code always gets the same label.
4. **Optional AI, your key.** With an Anthropic or OpenAI key, an LLM writes
   plain-English names and descriptions. You see a preview (request count,
   estimated tokens and cost, redacted secrets) and confirm before anything is
   sent. By default only structure is sent, never source code. AI can't lower
   a risk level.

## Supported today

Python with Flask, FastAPI, Django, Celery, click and typer. JavaScript and
TypeScript with Express and Next.js (pages and app router), BullMQ,
node-cron and commander. legacy2mcp also wraps existing **SOAP/WSDL services,
databases (Postgres, SQL Server, Oracle), REST APIs and plain websites**
directly, with the same safety model.

## Frequently asked questions

**Does my code leave my machine?**
No. The analysis is local. If you turn on AI enrichment, only the redacted
structure you previewed is sent, to the provider you chose, using your key.

**Do I need an API key?**
No. The full report, flows, risk labels, tool proposals, agent blueprint,
roadmap and generated server all work offline with zero tokens.

**How is this different from MCP server generators?**
Generators turn an API spec into tools. legacy2mcp starts from the code
itself, works out which business flows exist and which are dangerous, and
tells you what to build, why, and in what order, before generating
anything.

**What stops an agent from deleting data?**
Every tool that changes data is excluded from generated servers until you
opt in explicitly. Every call is validated against a JSON Schema and written
to an audit log. High-risk tools are labelled for human approval.

**Which languages are next?**
Java, .NET and Go are on the roadmap. Contributions are welcome.

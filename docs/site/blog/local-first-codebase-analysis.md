---
title: "Analyzing a codebase for AI agents without sending it anywhere"
description: "How legacy2mcp keeps codebase analysis local, with zero-token static analysis, optional bring-your-own-key AI, content-based secret redaction, a preview before every call, and a hard token budget."
date: 2026-11-23
tags: [privacy, security, static-analysis, llm]
---

# Analyzing a codebase for AI agents without sending it anywhere

Teams with legacy systems usually can't paste their code into a chat window.
legacy2mcp was built on that constraint. Here is what happens to your code
when you run it.

## The core is static and offline

`legacy2mcp analyze ./my-app --no-ai` does the whole job on your machine:
discovery (respecting `.gitignore` and a `.legacy2mcpignore`), parsing, call
and data-access graphs, flow tracing, rule-based risk, tool proposals, the
agent blueprint, the roadmap and the HTML report. It uses zero tokens and no
network. Nothing in the repo is imported or executed. Python goes through the
standard library's parser and JavaScript/TypeScript through tree-sitter.

The report is a single HTML file with its graph library inlined and a
Content-Security-Policy that blocks network requests, so opening it doesn't
phone home either. It records no absolute paths, so sharing it doesn't leak
your username.

## AI is optional, and you see everything first

With your own Anthropic or OpenAI key, an LLM can write plain-English flow
names, descriptions and tool documentation. Before any request:

- **A local plan.** legacy2mcp builds every payload locally and shows the number of
  requests, an estimated token count and cost, and how many secret-looking values
  it redacted.
- **A dry run.** `--ai-dry-run` writes the exact payloads to `ai-payload.json` so you
  can read them. Nothing is sent.
- **Confirmation.** You're asked to confirm. Non-interactive runs need `--yes`,
  otherwise they stay offline.

By default the payload is **structure only**: entry points, step labels like
"writes orders" or "charges via stripe", resource names, risk reasons and
parameter names. No file contents. `--ai-snippets` opts in to sending up to
40 lines per handler.

## Redaction by content, not by name

Every payload is scanned before it leaves: provider API keys, cloud and SaaS
tokens, JWTs, bearer tokens, private key blocks, credentials embedded in
connection strings (`postgres://user:[REDACTED]@host`), and
`password = "..."`-style assignments. The patterns lean broad on purpose: a
false positive costs a word of context, while a false negative leaks a
credential.

## Budgets and failure modes

`--max-tokens-budget` caps the total, and requests that wouldn't fit are
skipped rather than truncated. A refusal, timeout, malformed answer or spent
budget leaves the offline result in place. AI output is merged under fixed
rules: it can rename and describe, but it can't lower a risk level, mark a
write as safe, or change which tools are selected.

## Generated servers keep the same posture

`legacy2mcp generate` emits a config for legacy2mcp's existing MCP runtime.
Every call is schema-validated and audit-logged, and write tools stay hidden
until `allow_write_operations: true` is set explicitly.

*Try it: `pip install "legacy2mcp[analyze]" && legacy2mcp analyze ./my-app --no-ai`.
Source: [github.com/OWASP/legacy2mcp](https://github.com/OWASP/legacy2mcp).*

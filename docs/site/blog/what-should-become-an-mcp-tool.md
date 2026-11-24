---
title: "What in your codebase should become an MCP tool?"
description: "A practical way to decide which parts of an existing application to expose to AI agents: trace business flows, label risk from side effects, and ship read-only tools first."
date: 2026-11-23
tags: [mcp, ai-agents, legacy-modernization, security]
---

# What in your codebase should become an MCP tool?

Wrapping an API in a Model Context Protocol server takes minutes. Deciding
*which* operations an AI agent should be able to call is the hard part, and
it's the part most teams skip.

## Start from flows, not endpoints

An endpoint list says what *can* be called. It doesn't say what happens when
it is. `POST /orders/{id}/checkout` looks like one operation. Follow the code
and it reads the order, charges a card through a payment provider, updates the
order's status and sends a receipt email. Those are four side effects, two of
which leave your system for good.

So the unit of decision is the **business flow**: an entry point plus
everything it causes, in order. legacy2mcp builds these by parsing the code
locally (Python with `ast`, JavaScript/TypeScript with tree-sitter),
resolving calls across modules and recording every side effect along the way.

## Label risk by what a flow touches

Once you can see the side effects, risk stops being a matter of opinion:

| The flow... | Risk |
|---|---|
| only reads | read-only |
| writes your own data, enqueues work, writes local files | low-risk write |
| moves money, deletes records, sends data or email outside, changes credentials, or is an HTTP DELETE | high-risk |

Every label should come with its reasons and a file and line, so a reviewer can
check it in seconds. Writes with no visible authentication deserve their own
flag, whatever their level. A login endpoint is public by design and
shouldn't be.

## Ship in three phases

1. **Read-only tools.** Agents look things up and change nothing. This is where
   most of the early value comes from (status lookups, search, reporting), and
   the blast radius is tiny.
2. **Low-risk writes with approval.** A policy (or a person, at first) approves each
   call. Make every write attributable in an audit log and testable for rollback.
3. **Orchestration.** A coordinator routes requests to specialist agents per domain.
   High-risk tools sit behind human approval on every call.

## Try it on your code

```bash
pip install "legacy2mcp[analyze]"
legacy2mcp analyze ./my-app --no-ai
```

Open `legacy2mcp-analysis/report.html`. Each high-risk flow shows why it's
high-risk. The tools section starts with only read-only tools selected.
Export your selection and run `legacy2mcp generate` to get a server whose
write tools stay hidden until you enable them.

*legacy2mcp is open source (Apache 2.0) and an OWASP Incubator Project:
[github.com/bvenkata/legacy2mcp](https://github.com/bvenkata/legacy2mcp).*

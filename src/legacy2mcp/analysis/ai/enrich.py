"""
Stage 6 of `legacy2mcp analyze`: optional AI enrichment.

Plan first, send second. `plan_enrichment()` builds every request locally
-- structure only by default (entry points, step labels, resources, risk
reasons, params; never source code unless --ai-snippets), redacted -- and
estimates tokens and cost so the CLI can show a preview and ask before any
byte leaves the machine. `run_plan()` sends requests while they fit the token
budget and merges answers under fixed rules: AI may rename and describe
flows and tools and add notes, but never changes a risk level, a `safe`
flag or a default selection.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from legacy2mcp.analysis.ai.providers import Provider, ProviderError, estimate_cost
from legacy2mcp.analysis.ai.redact import redact
from legacy2mcp.analysis.model import Analysis, RiskReason

FLOWS_PER_REQUEST = 12
OUTPUT_TOKENS_PER_FLOW = 160
SNIPPET_MAX_LINES = 40
_TOOL_NAME_RE = re.compile(r"[^a-z0-9_]+")

NAME_FLOWS_SYSTEM = """\
You review static-analysis output for a software codebase and write the plain-English layer of a report
that helps a team decide which business flows to expose to AI agents as MCP tools.

For each flow you get its entry point (HTTP route, CLI command, scheduled job or message handler), the
ordered steps a static tracer found, the data it touches, and the rule-based risk level with reasons.

Return, for every flow id you were given:
- name: what the flow does for the business, 3-8 words, sentence case, no route syntax (e.g. "Charge a card and mark the order paid").
- description: 1-2 sentences an engineer or product owner can act on. Mention side effects that matter.
- risk_notes: one sentence on what a reviewer should check before exposing this flow, or "" when nothing stands out.
  Never argue that a flow is safer than its risk level; the level is fixed by rules.
- tool_name: for http_route flows, a snake_case MCP tool name (verb_object, <= 40 chars); "" otherwise.
- tool_description: for http_route flows, one sentence telling an AI agent when to call the tool and what it changes; "" otherwise.

Base everything on the data given. If the steps don't say what something does, describe it from its route
and resources rather than inventing behavior."""

NAME_FLOWS_SCHEMA = {
    "type": "object",
    "properties": {
        "flows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "risk_notes": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "tool_description": {"type": "string"},
                },
                "required": ["id", "name", "description", "risk_notes", "tool_name", "tool_description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["flows"],
    "additionalProperties": False,
}


@dataclass
class AIRequest:
    task: str
    system: str
    user: str  # redacted JSON payload, exactly what will be sent
    schema: dict
    max_tokens: int
    est_input_tokens: int


@dataclass
class AIPlan:
    provider: str
    model: str
    requests: list[AIRequest] = field(default_factory=list)
    redactions: int = 0

    @property
    def est_input_tokens(self) -> int:
        return sum(r.est_input_tokens for r in self.requests)

    @property
    def est_output_tokens(self) -> int:
        return sum(r.max_tokens for r in self.requests)

    @property
    def est_cost_usd(self) -> float | None:
        return estimate_cost(self.model, self.est_input_tokens, self.est_output_tokens)


def estimate_tokens(text: str) -> int:
    """Rough local estimate (~3.5 characters per token for code-heavy JSON).
    Used only for the preview and budget pre-check; actual usage comes from
    the provider's response."""
    return math.ceil(len(text) / 3.5)


def _snippet(root: Path | None, handler: str, analysis: Analysis) -> str | None:
    if root is None or "::" not in handler:
        return None
    path, qualname = handler.split("::", 1)
    fn = next((f for m in analysis.modules if m.path == path for f in m.functions if f.qualname == qualname), None)
    if fn is None:
        return None
    try:
        lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    body = lines[fn.line - 1: min(fn.end_line, fn.line - 1 + SNIPPET_MAX_LINES)]
    return "\n".join(body)


def _flow_payload(flow, analysis: Analysis, entries: dict, labels: dict, root: Path | None, snippets: bool) -> dict:
    entry = entries.get(flow.entry_name + "|" + flow.handler)
    item = {
        "id": flow.id,
        "kind": flow.entry_kind,
        "entry": flow.entry_name,
        "method": flow.method,
        "path": flow.path,
        "framework": flow.framework,
        "handler": flow.handler.split("::")[-1],
        "docstring": flow.description,
        "risk": flow.risk,
        "risk_reasons": [r.text for r in flow.reasons if r.level != "info"],
        "authenticated": flow.authenticated,
        "steps": [s.label for s in flow.steps if s.kind != "enter"][:30],
        "resources": [f"{labels.get(r.resource, r.resource)} ({r.op})" for r in flow.resources],
        "params": [{"name": p.name, "type": p.type, "in": p.location} for p in (entry.params if entry else [])],
    }
    if snippets:
        code = _snippet(root, flow.handler, analysis)
        if code:
            item["handler_source"] = code
    return item


def plan_enrichment(analysis: Analysis, provider: str, model: str, snippets: bool = False,
                    root: Path | None = None) -> AIPlan:
    plan = AIPlan(provider=provider, model=model)
    entries = {e.name + "|" + e.handler: e for e in analysis.entry_points}
    labels = {r.id: r.label for r in analysis.graph.resources}
    flows = analysis.flows
    for i in range(0, len(flows), FLOWS_PER_REQUEST):
        batch = flows[i: i + FLOWS_PER_REQUEST]
        payload = {
            "task": "name_flows",
            "project": analysis.project,
            "frameworks": analysis.summary.frameworks,
            "flows": [_flow_payload(f, analysis, entries, labels, root, snippets) for f in batch],
        }
        user, count = redact(json.dumps(payload, ensure_ascii=False))
        plan.redactions += count
        plan.requests.append(AIRequest(
            task="name_flows", system=NAME_FLOWS_SYSTEM, user=user, schema=NAME_FLOWS_SCHEMA,
            max_tokens=max(1024, OUTPUT_TOKENS_PER_FLOW * len(batch) + 400),
            est_input_tokens=estimate_tokens(NAME_FLOWS_SYSTEM + user),
        ))
    return plan


def _clean_tool_name(raw: str, used: set[str]) -> str | None:
    name = _TOOL_NAME_RE.sub("_", raw.strip().lower()).strip("_")[:60]
    if not name:
        return None
    base, n = name, 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    return name


def apply_flow_results(analysis: Analysis, result: dict) -> int:
    """Merge one `name_flows` answer. Returns the number of flows updated."""
    flows = {f.id: f for f in analysis.flows}
    tools = {t.flow_id: t for t in analysis.tools}
    used = {t.name for t in analysis.tools}
    updated = 0
    for item in result.get("flows", []) if isinstance(result, dict) else []:
        if not isinstance(item, dict):
            continue
        flow = flows.get(item.get("id"))
        if flow is None:
            continue  # the model invented an id: ignore
        name = str(item.get("name") or "").strip()
        if name:
            flow.name = name[:80]
        desc = str(item.get("description") or "").strip()
        if desc:
            flow.description = desc[:500]
        note = str(item.get("risk_notes") or "").strip()
        if note:
            flow.reasons.append(RiskReason("info", f"AI review note: {note[:300]}"))
        tool = tools.get(flow.id)
        if tool is not None:
            used.discard(tool.name)
            new_name = _clean_tool_name(str(item.get("tool_name") or ""), used)
            tool.name = new_name or tool.name
            used.add(tool.name)
            tdesc = str(item.get("tool_description") or "").strip()
            if tdesc:
                tool.description = tdesc[:500]
            tool.source = "ai"
        updated += 1
    return updated


@dataclass
class RunResult:
    requests_sent: int = 0
    requests_skipped: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)


def run_plan(plan: AIPlan, provider: Provider, analysis: Analysis, budget: int | None,
             appliers: dict | None = None) -> RunResult:
    """Send each request while (spent + estimate) fits the budget; merge answers.
    A failed request is recorded and skipped -- the rule-based result stands."""
    appliers = {"name_flows": apply_flow_results, **(appliers or {})}
    out = RunResult()
    for req in plan.requests:
        spent = out.input_tokens + out.output_tokens
        if budget is not None and spent + req.est_input_tokens + req.max_tokens > budget:
            out.requests_skipped += 1
            continue
        try:
            result, usage = provider.complete_json(req.system, req.user, req.schema, req.max_tokens)
        except ProviderError as exc:
            out.errors.append(str(exc))
            out.requests_skipped += 1
            continue
        out.requests_sent += 1
        out.input_tokens += usage.input_tokens
        out.output_tokens += usage.output_tokens
        applier = appliers.get(req.task)
        if applier is not None:
            applier(analysis, result)
    analysis.meta["ai"] = out.requests_sent > 0
    analysis.meta["ai_usage"] = {
        "provider": plan.provider,
        "model": plan.model,
        "requests": out.requests_sent,
        "skipped_requests": out.requests_skipped,
        "input_tokens": out.input_tokens,
        "output_tokens": out.output_tokens,
        "budget": budget,
        "redactions": plan.redactions,
        "errors": out.errors[:5],
    }
    return out

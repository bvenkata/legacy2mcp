"""
Stage 7 of `legacy2mcp analyze`: an agent orchestration blueprint, built by
rules from the flows and their risk, plus multimodal opportunities.

- One specialist agent per business domain (first meaningful path segment
  for HTTP flows, the main table/model otherwise), owning that domain's
  tools.
- One operations agent that knows the background flows (jobs, queue
  consumers, CLI commands). They aren't tools; agents should know they run.
- A coordinator that routes requests to specialists and holds no tools.
- An approval point on every write tool: "human" for high-risk, "policy"
  for low-risk.

Multimodal opportunities come from names in the code (params, routes,
handlers, resources), each with the evidence that triggered it. None are
invented: if nothing matches, the list is empty.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from legacy2mcp.analysis.model import (
    Agent,
    Analysis,
    ApprovalPoint,
    Flow,
    MultimodalOpportunity,
    Orchestration,
)
from legacy2mcp.analysis.risk import rules

_SKIP_SEGMENTS = {"api", "rest", "v1", "v2", "v3", "v4", "admin", "internal", "public"}

_MULTIMODAL_RULES = [
    ("document", re.compile(r"(upload|document|pdf|invoice|receipt|attachment|statement|contract|csv|xlsx|report|form|file)", re.I),
     "Document intake",
     "Let an agent read uploaded or emailed documents (PDFs, receipts, spreadsheets) and fill these flows' inputs, "
     "with the extracted fields shown to a person before any write."),
    ("image", re.compile(r"(image|photo|picture|avatar|scan|thumbnail|camera|selfie|id_?card|kyc|logo)", re.I),
     "Image understanding",
     "Let an agent inspect images (photos, scans, ID documents) to classify or extract details before these flows run."),
    ("voice", re.compile(r"(appointment|booking|reservation|order|ticket|support|status|call|phone|sms|schedule)", re.I),
     "Voice self-service",
     "Expose the read-only lookups in these flows to a voice agent (phone or assistant), so callers can check status "
     "without waiting for staff. Keep writes behind confirmation."),
]


def _domain(flow: Flow, analysis: Analysis) -> str:
    if flow.path:
        segs = [s for s in flow.path.strip("/").split("/") if s and not s.startswith("{") and s.lower() not in _SKIP_SEGMENTS]
        if segs:
            return re.sub(r"[^a-z0-9]+", "-", segs[0].lower()).strip("-") or "general"
    db = [r.resource.split(":", 1)[1] for r in flow.resources if r.resource.startswith("db:")]
    if db:
        return re.sub(r"[^a-z0-9]+", "-", db[0].lower()).strip("-")
    return "general"


def _title(domain: str) -> str:
    return domain.replace("-", " ").capitalize()


def build_orchestration(analysis: Analysis) -> Orchestration:
    tool_by_flow = {t.flow_id: t for t in analysis.tools}
    domains: OrderedDict[str, list[Flow]] = OrderedDict()
    background: list[Flow] = []
    for flow in sorted(analysis.flows, key=lambda f: f.id):
        if flow.entry_kind == "http_route":
            domains.setdefault(_domain(flow, analysis), []).append(flow)
        else:
            background.append(flow)

    agents: list[Agent] = []
    approvals: list[ApprovalPoint] = []
    for domain, flows in domains.items():
        tool_flows = [f.id for f in flows if f.id in tool_by_flow and tool_by_flow[f.id].exposable]
        risk = rules.max_level(*(f.risk for f in flows))
        writes = sum(1 for f in flows if f.risk != rules.READ_ONLY)
        agent = Agent(
            id=f"{domain}-agent",
            name=f"{_title(domain)} agent",
            role="specialist",
            domain=domain,
            description=(f"Handles {_title(domain).lower()} requests with {len(tool_flows)} "
                         f"{'tool' if len(tool_flows) == 1 else 'tools'}"
                         + (f"; {writes} {'changes' if writes == 1 else 'change'} data and need"
                            f"{'s' if writes == 1 else ''} approval." if writes else ", all read-only.")),
            flows=[f.id for f in flows],
            tool_flows=tool_flows,
            risk=risk,
        )
        agents.append(agent)
        for f in flows:
            if f.risk == rules.READ_ONLY or f.id not in tool_flows:
                continue
            why = next((r.text for r in f.reasons if r.level == f.risk), "changes data")
            approvals.append(ApprovalPoint(
                flow_id=f.id, agent=agent.id,
                level="human" if f.risk == rules.HIGH else "policy",
                reason=why,
            ))
    if background:
        agents.append(Agent(
            id="operations-agent",
            name="Operations agent",
            role="operations",
            domain="operations",
            description=(f"Knows the {len(background)} background flow(s) (scheduled jobs, queue consumers, CLI commands) "
                         "so other agents can explain delayed effects. It has no tools; these flows aren't exposed directly."),
            flows=[f.id for f in background],
            risk=rules.max_level(*(f.risk for f in background)),
        ))
    coordinator = Agent(
        id="coordinator",
        name="Coordinator",
        role="coordinator",
        description=("Understands the user's goal, routes it to the right specialist, and gathers approvals before any "
                     "write. It holds no tools itself, so every data change goes through an approval point."),
        flows=[],
    ) if agents else None
    return Orchestration(coordinator=coordinator, agents=agents, approvals=approvals)


def find_multimodal(analysis: Analysis) -> list[MultimodalOpportunity]:
    entries = {e.name + "|" + e.handler: e for e in analysis.entry_points}
    out: list[MultimodalOpportunity] = []
    for kind, pattern, title, description in _MULTIMODAL_RULES:
        flows: list[str] = []
        evidence: list[str] = []
        for flow in analysis.flows:
            if kind == "voice" and (flow.entry_kind != "http_route" or flow.risk != rules.READ_ONLY):
                continue  # voice suggestions only for read-only lookups
            entry = entries.get(flow.entry_name + "|" + flow.handler)
            texts = [flow.path or "", flow.handler.split("::")[-1]] + [p.name for p in (entry.params if entry else [])]
            texts += [r.resource for r in flow.resources]
            hits = sorted({m.group(0).lower() for t in texts for m in [pattern.search(t)] if m})
            if hits:
                flows.append(flow.id)
                evidence.append(f"{flow.entry_name}: {', '.join(hits)}")
        if flows:
            out.append(MultimodalOpportunity(kind=kind, title=title, description=description,
                                             flows=flows, evidence=evidence[:8]))
    return out

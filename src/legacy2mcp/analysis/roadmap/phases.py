"""
Stage 8 of `legacy2mcp analyze`: a phased roadmap from the tool proposals,
flows and orchestration blueprint.

Phase 1 ships read-only tools. Phase 2 adds low-risk writes behind policy
approval. Phase 3 brings in high-risk tools behind human approval, plus the
full coordinator/specialist setup and any multimodal intake. Effort is a
rough planning estimate (setup plus a per-tool cost, more for riskier
tools), labelled S/M/L. It is a starting point for planning, not a quote.
"""

from __future__ import annotations

from legacy2mcp.analysis.model import Analysis, Phase, Roadmap
from legacy2mcp.analysis.risk import rules

_DAYS_PER_TOOL = {rules.READ_ONLY: 0.5, rules.LOW: 1.0, rules.HIGH: 2.0}
_SETUP_DAYS = {1: 2, 2: 2, 3: 4}


def _effort(days: float) -> str:
    return "S" if days <= 5 else "M" if days <= 15 else "L"


def build_roadmap(analysis: Analysis) -> Roadmap:
    tools = [t for t in analysis.tools if t.exposable]
    flows = {f.id: f for f in analysis.flows}
    by_risk = {lvl: [t for t in tools if t.risk == lvl] for lvl in rules.RISK_LEVELS}
    unauth_writes = [f for f in analysis.flows if f.authenticated is False and f.risk != rules.READ_ONLY]
    untyped = [t for t in tools if "not inferred" in str(t.input_schema)]
    phases: list[Phase] = []

    p1 = by_risk[rules.READ_ONLY]
    days1 = _SETUP_DAYS[1] + _DAYS_PER_TOOL[rules.READ_ONLY] * len(p1)
    phases.append(Phase(
        id="phase-1", name="Phase 1: Read-only tools",
        summary=(f"Expose {len(p1)} read-only tool(s) through `legacy2mcp generate`. Agents can look things up but "
                 "change nothing; every call is schema-validated and audit-logged.") if p1 else
                "No read-only HTTP flows were found; start by adding a lookup endpoint or review the excluded paths.",
        effort=_effort(days1), effort_days=round(days1), risk=rules.READ_ONLY,
        tool_flows=[t.flow_id for t in p1],
        prerequisites=["Review each tool's description and input schema in the report", "Point the generated config at a staging base URL"],
        exit_criteria=["Agents answer real questions using only these tools", "Audit log reviewed for a week with no surprises"],
    ))

    p2 = by_risk[rules.LOW]
    days2 = _SETUP_DAYS[2] + _DAYS_PER_TOOL[rules.LOW] * len(p2)
    prereq2 = ["Decide the approval policy (who approves, which writes are auto-approved)"]
    if unauth_writes:
        prereq2.append(f"Add authentication to {len(unauth_writes)} write flow(s) that showed none")
    if untyped:
        prereq2.append(f"Write explicit body schemas for {len(untyped)} tool(s) whose fields weren't inferred")
    phases.append(Phase(
        id="phase-2", name="Phase 2: Low-risk writes with approval",
        summary=(f"Add {len(p2)} low-risk write tool(s) with `allow_write_operations: true`, each behind a policy "
                 "approval and rate limits.") if p2 else
                "No low-risk write tools were proposed; use this phase to put approvals and auth in place for phase 3.",
        effort=_effort(days2), effort_days=round(days2), risk=rules.LOW if p2 else rules.READ_ONLY,
        tool_flows=[t.flow_id for t in p2],
        prerequisites=prereq2,
        exit_criteria=["Every write is attributable to an approver in the audit log", "Rollback path tested for each write tool"],
    ))

    p3 = by_risk[rules.HIGH]
    agents = [a for a in analysis.orchestration.agents if a.role == "specialist"]
    days3 = _SETUP_DAYS[3] + _DAYS_PER_TOOL[rules.HIGH] * len(p3) + len(agents) + 3 * len(analysis.multimodal)
    reasons = sorted({r.text.split(" (")[0] for t in p3 for r in flows[t.flow_id].reasons if r.level == rules.HIGH})
    phases.append(Phase(
        id="phase-3", name="Phase 3: Agent orchestration",
        summary=(f"Run a coordinator with {len(agents)} specialist agent(s); add {len(p3)} high-risk tool(s) "
                 "(" + "; ".join(reasons[:3]) + ") behind human approval on every call"
                 + (f", and {len(analysis.multimodal)} multimodal intake path(s)." if analysis.multimodal else ".")) if p3 else
                f"Run a coordinator with {len(agents)} specialist agent(s) over the phase 1-2 tools"
                + (f" and add {len(analysis.multimodal)} multimodal intake path(s)." if analysis.multimodal else "."),
        effort=_effort(days3), effort_days=round(days3), risk=rules.HIGH if p3 else rules.max_level(*(t.risk for t in tools)),
        tool_flows=[t.flow_id for t in p3],
        prerequisites=["Human-approval UI or chat step wired to the coordinator", "Spending/deletion limits agreed with the business owner",
                       "Incident runbook for a bad agent action"],
        exit_criteria=["High-risk calls happen only after a recorded human approval", "End-to-end flows succeed on staging data"],
    ))
    return Roadmap(phases=phases)

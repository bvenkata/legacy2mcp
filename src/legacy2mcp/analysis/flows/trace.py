"""
Stage 4 of `legacy2mcp analyze`: trace a business flow from every entry
point through the call graph.

A flow is the ordered list of what happens when an entry point fires: the
handler, each internal function it calls (depth-first, in source order), and
every side effect along the way. Work handed to a queue (Celery `.delay()`)
is followed too and marked as background. Each function is expanded once per
flow; later calls to it show as "seen" so recursive or diamond-shaped call
graphs stay readable.
"""

from __future__ import annotations

import re

from legacy2mcp.analysis.graph.build import resource_for
from legacy2mcp.analysis.model import (
    Analysis,
    CallEdge,
    EntryPoint,
    Flow,
    FlowResource,
    FlowStep,
    Function,
    RiskReason,
)
from legacy2mcp.analysis.risk import rules

MAX_DEPTH = 8
MAX_STEPS = 150
_OP_RANK = {"read": 0, "unknown": 1, "write": 2}
_EFFECT_VERB = {
    ("db", "read"): "reads", ("db", "write"): "writes", ("db", "unknown"): "queries",
    ("http_out", "read"): "fetches from", ("http_out", "write"): "sends to", ("http_out", "unknown"): "calls",
    ("payment", "read"): "looks up", ("payment", "write"): "charges/changes via",
    ("email", "write"): "sends email via", ("queue", "write"): "enqueues to",
    ("file", "write"): "writes", ("auth", "read"): "checks", ("auth", "write"): "changes",
}


def _runs_for(func: Function, line: int, method: str) -> bool:
    """False if `line` sits only inside branches for other HTTP methods."""
    covering = [b for b in func.method_branches if b.start <= line <= b.end]
    return not covering or any(b.method == method for b in covering)


def short(key: str) -> str:
    """'shop/orders.py::checkout' -> 'checkout'."""
    return key.split("::", 1)[-1]


def flow_slug(entry: EntryPoint) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", entry.name.lower()).strip("-")
    return slug or entry.kind


def _plain_name(entry: EntryPoint, handler: Function | None) -> str:
    if handler is not None and handler.docstring:
        return handler.docstring.rstrip(".")
    return entry.name


class _Tracer:
    def __init__(self, analysis: Analysis):
        self.functions: dict[str, Function] = {
            f"{m.path}::{f.qualname}": f for m in analysis.modules for f in m.functions
        }
        self.out: dict[str, list[CallEdge]] = {}
        for e in analysis.graph.call_edges:
            self.out.setdefault(e.source, []).append(e)
        self.labels = {r.id: r.label for r in analysis.graph.resources}

    def trace(self, entry: EntryPoint) -> Flow:
        handler = self.functions.get(entry.handler)
        flow = Flow(
            id=flow_slug(entry),
            name=_plain_name(entry, handler),
            entry_kind=entry.kind,
            entry_name=entry.name,
            handler=entry.handler,
            framework=entry.framework,
            method=entry.method,
            path=entry.path,
            description=handler.docstring if handler else "",
            middleware=list(entry.middleware),
        )
        if handler is None:
            flow.reasons.append(RiskReason("info", "handler could not be resolved to a function in this repo",
                                           f"{entry.file}:{entry.line}"))
            flow.authenticated = rules.is_authenticated(entry, False)
            return flow

        expanded: set[str] = set()
        resources: dict[str, str] = {}
        level = rules.READ_ONLY
        reasons: list[RiskReason] = []
        seen_reasons: set[tuple[str, str]] = set()
        auth_read = False

        def add(step: FlowStep) -> bool:
            if len(flow.steps) >= MAX_STEPS:
                flow.truncated = True
                return False
            step.order = len(flow.steps) + 1
            flow.steps.append(step)
            return True

        def visit(key: str, depth: int, background: bool) -> None:
            nonlocal level, auth_read
            expanded.add(key)
            func = self.functions[key]
            file = key.split("::", 1)[0]
            events: list[tuple[int, int, object]] = [(e.line, 0, e) for e in func.side_effects]
            events += [(e.line, 1, e) for e in self.out.get(key, [])]
            events.sort(key=lambda t: (t[0], t[1]))
            if depth == 0 and entry.kind == "http_route" and entry.method and func.method_branches:
                events = [ev for ev in events if _runs_for(func, ev[0], entry.method)]
            for _, kind, item in events:
                if kind == 0:  # side effect
                    eff = item
                    res = resource_for(eff)
                    resources[res.id] = max(resources.get(res.id, "read"), eff.op, key=lambda o: _OP_RANK.get(o, 0))
                    step_level, why = rules.classify_effect(eff, self.labels.get(res.id, ""))
                    if eff.kind == "auth" and eff.op == "read":
                        auth_read = True
                    if why:
                        level = rules.max_level(level, step_level)
                        if (step_level, why) not in seen_reasons:
                            seen_reasons.add((step_level, why))
                            reasons.append(RiskReason(step_level, why, f"{file}:{eff.line}"))
                    verb = _EFFECT_VERB.get((eff.kind, eff.op), f"{eff.kind} {eff.op}")
                    label = f"{verb} {self.labels.get(res.id, res.label)}" + (" (background)" if background else "")
                    if not add(FlowStep(0, depth, "effect", key, eff.line, label, eff.kind, eff.op, res.id, step_level)):
                        return
                else:  # call edge
                    edge = item
                    target = edge.target
                    is_async = edge.kind == "async"
                    if target in expanded:
                        if not add(FlowStep(0, depth, "seen", target, edge.line, f"calls {short(target)} (shown above)")):
                            return
                        continue
                    label = (f"enqueues {short(target)} to run in the background" if is_async
                             else f"calls {short(target)}")
                    if not add(FlowStep(0, depth + 1, "async" if is_async else "call", target, edge.line, label)):
                        return
                    if depth + 1 < MAX_DEPTH and target in self.functions:
                        visit(target, depth + 1, background or is_async)
                    elif depth + 1 >= MAX_DEPTH:
                        flow.truncated = True

        add(FlowStep(0, 0, "enter", entry.handler, handler.line, f"{entry.name} -> {short(entry.handler)}"))
        visit(entry.handler, 0, False)

        reasons += rules.entry_reasons(entry, level, short(entry.handler))
        level = rules.max_level(level, *(r.level for r in reasons))
        flow.authenticated = rules.is_authenticated(entry, auth_read)
        if flow.authenticated is False and level != rules.READ_ONLY:
            reasons.append(RiskReason("info", "writes data but no authentication middleware or check was detected -- verify access control",
                                      f"{entry.file}:{entry.line}"))
        if flow.truncated:
            reasons.append(RiskReason("info", f"flow truncated (depth > {MAX_DEPTH} or > {MAX_STEPS} steps)"))
        flow.risk = level
        flow.reasons = reasons
        flow.functions = sorted(expanded)
        flow.resources = [FlowResource(resource=r, op=o) for r, o in sorted(resources.items())]
        return flow


def trace_flows(analysis: Analysis) -> list[Flow]:
    tracer = _Tracer(analysis)
    flows: list[Flow] = []
    used: dict[str, int] = {}
    for entry in analysis.entry_points:
        flow = tracer.trace(entry)
        n = used.get(flow.id, 0)
        used[flow.id] = n + 1
        if n:
            flow.id = f"{flow.id}-{n + 1}"
        flows.append(flow)
    return flows

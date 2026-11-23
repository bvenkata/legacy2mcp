"""
Offline MCP tool proposals: one per HTTP flow, built from the route's
params and the flow's risk. AI enrichment (M5) may rewrite names and
descriptions; it never flips `safe`, and writes always start excluded.
"""

from __future__ import annotations

import re

from legacy2mcp.analysis.model import Analysis, Flow, Param, ToolProposal
from legacy2mcp.analysis.risk import rules

_EXPOSABLE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_METHOD_VERB = {"GET": "get", "POST": "create", "PUT": "replace", "PATCH": "update", "DELETE": "delete"}
_SKIP_SEGMENTS = {"api", "rest", "v1", "v2", "v3", "v4"}
_TYPE_MAP = {
    "int": "integer", "integer": "integer", "float": "number", "number": "number", "decimal": "number",
    "bool": "boolean", "boolean": "boolean", "str": "string", "string": "string",
}
_LIST_RE = re.compile(r"^(?:list|List|Sequence|tuple)\[|\[\]$|^Array<")
_OPTIONAL_RE = re.compile(r"^(?:Optional\[(.+)\]|(.+?)\s*\|\s*None|None\s*\|\s*(.+))$")
_LOCATION_TEXT = {"path": "URL path parameter", "query": "query-string parameter", "body": "request body field",
                  "form": "form field"}


def tool_name(method: str | None, path: str | None, used: set[str]) -> str:
    segs = [s for s in (path or "").strip("/").split("/") if s and s.lower() not in _SKIP_SEGMENTS]
    verb = _METHOD_VERB.get(method or "", (method or "call").lower())
    # POST /invoices/{id}/refund -> refund_invoices_by_id: the trailing literal is the action.
    if method == "POST" and len(segs) >= 2 and not segs[-1].startswith("{") and segs[-2].startswith("{"):
        verb, segs = segs[-1], segs[:-1]
    elif method == "GET" and segs and not segs[-1].startswith("{") and segs[-1].endswith("s"):
        verb = "list"
    words: list[str] = []
    for s in segs:
        if s.startswith("{") and s.endswith("}"):
            words += ["by", s[1:-1]]
        else:
            words.append(s)
    raw = "_".join([verb, *words]) or verb
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    name = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")[:60] or "tool"
    base, n = name, 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def json_type(annotation: str | None) -> dict:
    if not annotation:
        return {"type": "string"}
    ann = annotation.strip()
    m = _OPTIONAL_RE.match(ann)
    if m:
        ann = next(g for g in m.groups() if g)
    if _LIST_RE.search(ann):
        return {"type": "array"}
    base = _TYPE_MAP.get(ann.split("[")[0].lower())
    if base:
        return {"type": base}
    return {"type": "object", "description": f"{ann} (structure not inferred -- review)"}


def input_schema(params: list[Param]) -> dict:
    props: dict[str, dict] = {}
    required: list[str] = []
    for p in params:
        if p.name.startswith(("...", "{")):
            continue
        schema = json_type(p.type)
        where = _LOCATION_TEXT.get(p.location or "", "")
        schema["description"] = "; ".join(x for x in (schema.get("description"), where) if x)
        if not schema["description"]:
            del schema["description"]
        props[p.name] = schema
        if p.required:
            required.append(p.name)
    out: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def _description(flow: Flow, labels: dict[str, str]) -> str:
    lead = flow.description.rstrip(".") if flow.description else f"{flow.method} {flow.path}"
    reads = sorted({labels.get(r.resource, r.resource) for r in flow.resources if r.op == "read"})
    writes = sorted({labels.get(r.resource, r.resource) for r in flow.resources if r.op != "read"})
    parts = [lead + "."]
    if reads:
        parts.append("Reads " + ", ".join(reads) + ".")
    if writes:
        parts.append("Changes " + ", ".join(writes) + ".")
    return " ".join(parts)


def propose_tools(analysis: Analysis) -> list[ToolProposal]:
    entries = {e.name + "|" + e.handler: e for e in analysis.entry_points}
    labels = {r.id: r.label for r in analysis.graph.resources}
    used: set[str] = set()
    tools: list[ToolProposal] = []
    for flow in analysis.flows:
        if flow.entry_kind != "http_route":
            continue
        entry = entries.get(flow.entry_name + "|" + flow.handler)
        exposable = flow.method in _EXPOSABLE_METHODS and bool(flow.path)
        safe = flow.risk == rules.READ_ONLY
        notes = []
        if not exposable:
            notes.append(f"method {flow.method or '?'} isn't concrete; `generate` can't wire it without review")
        if flow.authenticated is False and not safe:
            notes.append("no authentication detected on this write")
        if not flow.steps:
            notes.append("handler not resolved; inputs may be incomplete")
        tools.append(
            ToolProposal(
                name=tool_name(flow.method, flow.path, used),
                flow_id=flow.id,
                description=_description(flow, labels),
                risk=flow.risk,
                safe=safe,
                include=safe and exposable,
                exposable=exposable,
                method=flow.method,
                path=flow.path,
                input_schema=input_schema(entry.params if entry else []),
                notes=notes,
            )
        )
    return tools

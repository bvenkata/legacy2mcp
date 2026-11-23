"""
Stage 10: `legacy2mcp generate` -- turn the approved tool proposals into a
runnable MCP server config for the existing `type: rest` adapter.

Generating a config rather than server code means every generated tool
inherits the runtime that `legacy2mcp run` already has: JSON-Schema
validation of every call, audit logging, and write tools hidden unless
`allow_write_operations: true` is set. This module never sets that flag on
its own; `--allow-writes` does, explicitly.

What gets selected, in order of precedence:
1. `flows`: explicit flow ids or tool names (`--flows a,b`).
2. `selections`: the `selections.json` exported from the HTML report.
3. Otherwise each proposal's default (`include`): read-only tools only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from legacy2mcp.analysis.model import Analysis, ToolProposal
from legacy2mcp.analysis.risk import rules
from legacy2mcp.config import Legacy2McpConfig

SELECTIONS_SCHEMA = "legacy2mcp-selections/1"
_PRIMITIVE = {"string", "integer", "number", "boolean"}


class GenerateError(Exception):
    pass


@dataclass
class GenerateResult:
    config: dict
    todos: list[str] = field(default_factory=list)
    selected: list[ToolProposal] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # human-readable reasons


def load_selections(path: str | Path) -> dict[str, bool]:
    """name -> include, plus "flow:<id>" -> include, from a report export."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GenerateError(f"could not read selections file '{path}': {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SELECTIONS_SCHEMA or not isinstance(data.get("tools"), list):
        raise GenerateError(f"'{path}' is not a {SELECTIONS_SCHEMA} file exported from the legacy2mcp report")
    out: dict[str, bool] = {}
    for t in data["tools"]:
        if isinstance(t, dict) and "include" in t:
            if t.get("name"):
                out[str(t["name"])] = bool(t["include"])
            if t.get("flow_id"):
                out[f"flow:{t['flow_id']}"] = bool(t["include"])
    return out


def _select(analysis: Analysis, flows: list[str] | None, selections: dict[str, bool] | None) -> tuple[list[ToolProposal], list[str]]:
    skipped: list[str] = []
    if flows:
        wanted = {f.strip() for f in flows if f.strip()}
        chosen = [t for t in analysis.tools if t.flow_id in wanted or t.name in wanted]
        found = {t.flow_id for t in chosen} | {t.name for t in chosen}
        missing = sorted(wanted - found)
        non_http = {f.id for f in analysis.flows if f.entry_kind != "http_route"}
        for m in missing:
            if m in non_http:
                skipped.append(f"{m}: not an HTTP flow, so it can't be exposed as a REST tool")
            else:
                skipped.append(f"{m}: no such flow id or tool name in this analysis")
    elif selections is not None:
        chosen = [t for t in analysis.tools if selections.get(t.name, selections.get(f"flow:{t.flow_id}", False))]
    else:
        chosen = [t for t in analysis.tools if t.include]
    out = []
    for t in chosen:
        if not t.exposable:
            skipped.append(f"{t.name}: {'; '.join(t.notes) or 'not exposable'}")
            continue
        out.append(t)
    return out, skipped


def _endpoint(tool: ToolProposal, analysis: Analysis, todos: list[str]) -> dict:
    entry = next((e for e in analysis.entry_points
                  if e.kind == "http_route" and e.method == tool.method and e.path == tool.path), None)
    flow = next((f for f in analysis.flows if f.id == tool.flow_id), None)
    props = tool.input_schema.get("properties", {})
    required = set(tool.input_schema.get("required", []))
    location_of = {p.name: p.location for p in (entry.params if entry else [])}
    params = []
    review = bool(tool.notes) or tool.risk == rules.HIGH
    for name, schema in props.items():
        jtype = schema.get("type", "string")
        location = location_of.get(name) or ("path" if f"{{{name}}}" in (tool.path or "") else "query")
        if location == "form":
            todos.append(f"{tool.name}: '{name}' is a form field in the app; the REST adapter sends a JSON body -- confirm the app accepts JSON")
            location = "body"
        if jtype not in _PRIMITIVE:
            todos.append(f"{tool.name}: parameter '{name}' is a structured {jtype} ({schema.get('description', '').split(' (')[0] or 'unknown shape'}); "
                         "list its fields as separate body params")
            review = True
            continue
        params.append({"name": name, "location": location, "type": jtype, "required": name in required})
    write = tool.risk != rules.READ_ONLY or tool.method != "GET"
    description = tool.description
    if flow and flow.risk == rules.HIGH:
        description += " HIGH-RISK: require a human to approve every call."
    endpoint = {
        "name": tool.name,
        "method": tool.method,
        "path": tool.path,
        "description": description,
        "params": params,
        "write": write,
        "confidence": "medium",
        "needs_review": review,
    }
    return endpoint


def build_config(analysis: Analysis, flows: list[str] | None = None, selections: dict[str, bool] | None = None,
                 base_url: str | None = None, allow_writes: bool = False, token_env: str | None = None,
                 server_name: str | None = None) -> GenerateResult:
    selected, skipped = _select(analysis, flows, selections)
    todos: list[str] = []
    if not selected:
        raise GenerateError("no tools selected" + (f" ({'; '.join(skipped)})" if skipped else "")
                            + ". Pass --flows <ids>, a --selections file from the report, or check the report's tool list.")
    endpoints = [_endpoint(t, analysis, todos) for t in selected]

    writes = [e for e in endpoints if e["write"]]
    high = [t.name for t in selected if t.risk == rules.HIGH]
    if writes and not allow_writes:
        todos.append(f"{len(writes)} write tool(s) are in the config but stay hidden until you set "
                     "allow_write_operations: true (or re-run with --allow-writes)")
    if high:
        todos.append("High-risk tools (" + ", ".join(high) + ") need a human approval step: legacy2mcp validates and "
                     "audit-logs every call but does not pause for approval -- configure your MCP client to confirm "
                     "each call to these tools")
    unauth = [t.name for t in selected if t.risk != rules.READ_ONLY
              and next((f for f in analysis.flows if f.id == t.flow_id and f.authenticated is False), None)]
    if unauth:
        todos.append("No authentication was detected on: " + ", ".join(unauth) + " -- confirm access control in the app")
    if base_url is None:
        todos.append("Set base_url to where the app is running (staging first)")
    auth: dict = {"type": "none"}
    if token_env:
        auth = {"type": "bearer", "token_env": token_env}
    elif any(f.authenticated for f in analysis.flows if f.id in {t.flow_id for t in selected}):
        todos.append("Some selected flows check authentication; set auth: {type: bearer, token_env: <ENV VAR>} "
                     "(or re-run with --auth-token-env)")

    name = server_name or re.sub(r"[^a-z0-9-]+", "-", analysis.project.lower()).strip("-") or "legacy2mcp"
    config = {
        "server": {"name": f"{name}-mcp"},
        "adapters": [{
            "id": re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "app",
            "type": "rest",
            "config": {
                "base_url": base_url or "REPLACE_ME_BASE_URL",
                "auth": auth,
                "allow_write_operations": bool(allow_writes),
                "timeout_seconds": 15,
                "endpoints": endpoints,
            },
        }],
        "security": {"audit": {"enabled": True, "path": "./legacy2mcp-audit.log"}},
    }
    # The runtime's own schema is the contract: fail here, not at `run` time.
    Legacy2McpConfig.model_validate(config)
    return GenerateResult(config=config, todos=todos, selected=selected, skipped=skipped)

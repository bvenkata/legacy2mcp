"""
Typed, serializable result of `legacy2mcp analyze`.

Every pipeline stage adds its own top-level section to one `Analysis`
object, which is saved as a single JSON file (`analysis.json`). `report`
and `generate` read that file back, so nothing downstream ever has to
re-scan the codebase.

Output is deterministic on purpose: paths are repo-relative POSIX strings,
lists are sorted, and the only run-specific values (timestamp, tool
version, absolute root) live under `meta`, so golden-file tests can drop
that one key and compare the rest byte for byte.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

# Side-effect kinds a call site can be tagged with. `op` on a SideEffect is
# "read" or "write" where that distinction is knowable from the call alone.
SIDE_EFFECT_KINDS = ("db", "http_out", "email", "payment", "queue", "file", "auth")

# Risk levels, lowest to highest. "info" reasons never raise a level.
RISK_LEVELS = ("read_only", "low_risk_write", "high_risk")

# Entry-point kinds the flow tracer (M3) starts from.
ENTRY_POINT_KINDS = ("http_route", "cli_command", "scheduled_job", "message_handler")


@dataclass
class SideEffect:
    kind: str  # one of SIDE_EFFECT_KINDS
    op: str  # "read" | "write" | "unknown"
    call: str  # the call expression as written, e.g. "stripe.Charge.create"
    line: int
    detail: str = ""  # e.g. the model/table name, URL literal, SQL verb
    verb: str = ""  # the operation: SQL verb (DELETE) or method name (destroy, refunds.create -> create)


@dataclass
class Param:
    name: str
    type: str | None = None  # annotation as written, if any
    required: bool = True
    # Where a caller supplies it, when known: "path" | "query" | "body" | "form".
    # None for a plain function parameter that isn't an HTTP input.
    location: str | None = None


@dataclass
class MethodBranch:
    """Lines of a handler that only run for one HTTP method
    (`if request.method == "POST":` body, `case "DELETE":` block)."""

    method: str
    start: int
    end: int


@dataclass
class Function:
    qualname: str  # "func" or "Class.method"
    line: int
    end_line: int
    params: list[Param] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # callee names as written, sorted, unique
    side_effects: list[SideEffect] = field(default_factory=list)
    docstring: str = ""  # first line only
    is_async: bool = False
    # HTTP methods this function visibly handles when used as a view
    # (`@require_POST`, `@api_view(["GET"])`, `if request.method == "POST"`).
    http_methods: list[str] = field(default_factory=list)
    call_sites: dict[str, int] = field(default_factory=dict)  # call name -> first line it's called on
    method_branches: list[MethodBranch] = field(default_factory=list)


@dataclass
class Class:
    name: str
    line: int
    bases: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    docstring: str = ""


@dataclass
class Module:
    path: str  # repo-relative POSIX path
    language: str
    name: str  # dotted module name for Python, path without extension otherwise
    lines: int
    imports: dict[str, str] = field(default_factory=dict)  # local alias -> fully qualified target
    # JS/TS: exported name -> local qualname ("default" for a default export /
    # `module.exports = x`). Python modules export every top-level name, so
    # this stays empty for them.
    exports: dict[str, str] = field(default_factory=dict)
    functions: list[Function] = field(default_factory=list)
    classes: list[Class] = field(default_factory=list)
    parse_error: str | None = None


@dataclass
class EntryPoint:
    kind: str  # one of ENTRY_POINT_KINDS
    framework: str
    handler: str  # "<module path>::<qualname>", or an unresolved dotted name
    file: str  # file where the entry point is declared
    line: int
    name: str  # stable id-ish label, e.g. "GET /orders/{order_id}"
    method: str | None = None
    path: str | None = None
    params: list[Param] = field(default_factory=list)
    # Middleware / decorators wrapping the handler, as qualified names
    # (`src/middleware/auth.requireAuth`, `flask_login.login_required`).
    middleware: list[str] = field(default_factory=list)


@dataclass
class LanguageStats:
    files: int = 0
    lines: int = 0


@dataclass
class ScanInfo:
    included_files: int = 0
    excluded: dict[str, int] = field(default_factory=dict)  # reason -> count
    ignore_files: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # e.g. a language found but not parsed


@dataclass
class Summary:
    files: int = 0
    lines: int = 0
    languages: dict[str, LanguageStats] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    entry_points: int = 0
    functions: int = 0
    side_effects: dict[str, int] = field(default_factory=dict)  # kind -> count
    flows: int = 0
    risk: dict[str, int] = field(default_factory=dict)  # risk level -> number of flows


@dataclass
class CallEdge:
    source: str  # "<file>::<qualname>"
    target: str
    kind: str = "call"  # "call" | "async" (enqueued, e.g. Celery .delay())
    line: int = 0  # first call site in the source function


@dataclass
class ModuleEdge:
    source: str  # module path
    target: str
    names: list[str] = field(default_factory=list)  # imported names, sorted


@dataclass
class Resource:
    id: str  # e.g. "db:Order", "http:api.stripe.com", "payment:stripe"
    kind: str  # one of SIDE_EFFECT_KINDS
    label: str


@dataclass
class DataEdge:
    function: str  # "<file>::<qualname>"
    resource: str  # Resource.id
    op: str  # "read" | "write" | "unknown"


@dataclass
class Graph:
    call_edges: list[CallEdge] = field(default_factory=list)
    module_edges: list[ModuleEdge] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    data_edges: list[DataEdge] = field(default_factory=list)
    # internal_calls / unresolved_calls / external_packages counts
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskReason:
    level: str  # one of RISK_LEVELS, or "info"
    text: str
    where: str = ""  # "<file>:<line>" when it comes from one step


@dataclass
class FlowStep:
    order: int
    depth: int  # call depth from the entry point's handler (0 = the handler itself)
    kind: str  # "enter" | "call" | "async" | "effect" | "seen" (already expanded above)
    function: str  # "<file>::<qualname>" this step happens in (or enters)
    line: int
    label: str  # plain-English one-liner
    effect_kind: str | None = None
    op: str | None = None
    resource: str | None = None  # Resource.id
    risk: str = "read_only"


@dataclass
class FlowResource:
    resource: str  # Resource.id
    op: str  # strongest op seen: "write" > "unknown" > "read"


@dataclass
class Flow:
    id: str  # stable slug, e.g. "post-api-orders-id-pay"
    name: str  # plain-English name; AI enrichment may replace it
    entry_kind: str
    entry_name: str  # EntryPoint.name
    handler: str
    framework: str
    method: str | None = None
    path: str | None = None
    description: str = ""
    steps: list[FlowStep] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    resources: list[FlowResource] = field(default_factory=list)
    risk: str = "read_only"
    reasons: list[RiskReason] = field(default_factory=list)
    authenticated: bool | None = None  # None = not applicable / can't tell
    middleware: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ToolProposal:
    """An MCP tool `generate` could expose for one flow."""

    name: str  # MCP tool name: [a-z0-9_], <= 64 chars, unique
    flow_id: str
    description: str
    risk: str
    safe: bool  # read-only flow
    include: bool  # default selection; writes start excluded
    exposable: bool  # can `generate` wire it (an HTTP route with a concrete method)?
    method: str | None = None
    path: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    source: str = "rules"  # "rules" | "ai"


@dataclass
class Agent:
    id: str
    name: str
    role: str  # "coordinator" | "specialist" | "operations"
    description: str
    domain: str = ""
    flows: list[str] = field(default_factory=list)  # flow ids it owns
    tool_flows: list[str] = field(default_factory=list)  # flow ids exposed to it as tools
    risk: str = "read_only"


@dataclass
class ApprovalPoint:
    flow_id: str
    agent: str  # Agent.id that would call the tool
    level: str  # "human" (high-risk: a person approves every call) | "policy" (low-risk: rule or batch approval)
    reason: str


@dataclass
class Orchestration:
    coordinator: Agent | None = None
    agents: list[Agent] = field(default_factory=list)
    approvals: list[ApprovalPoint] = field(default_factory=list)
    notes: str = ""


@dataclass
class MultimodalOpportunity:
    kind: str  # "document" | "image" | "voice"
    title: str
    description: str
    flows: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    source: str = "rules"


@dataclass
class Phase:
    id: str
    name: str
    summary: str
    effort: str  # "S" | "M" | "L"
    effort_days: int
    risk: str  # one of RISK_LEVELS: the riskiest thing the phase ships
    tool_flows: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    exit_criteria: list[str] = field(default_factory=list)


@dataclass
class Roadmap:
    phases: list[Phase] = field(default_factory=list)


@dataclass
class Analysis:
    project: str  # basename of the scanned root
    summary: Summary = field(default_factory=Summary)
    scan: ScanInfo = field(default_factory=ScanInfo)
    modules: list[Module] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)
    graph: Graph = field(default_factory=Graph)
    flows: list[Flow] = field(default_factory=list)
    tools: list[ToolProposal] = field(default_factory=list)
    orchestration: Orchestration = field(default_factory=Orchestration)
    multimodal: list[MultimodalOpportunity] = field(default_factory=list)
    roadmap: Roadmap = field(default_factory=Roadmap)
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Put schema_version first for readability of the saved file.
        return {"schema_version": data.pop("schema_version"), **data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Analysis:
        version = str(data.get("schema_version", ""))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported analysis schema_version {version!r} (this legacy2mcp reads {SCHEMA_VERSION!r})."
            )
        summary = data.get("summary", {})
        return cls(
            project=data["project"],
            summary=Summary(
                files=summary.get("files", 0),
                lines=summary.get("lines", 0),
                languages={k: LanguageStats(**v) for k, v in summary.get("languages", {}).items()},
                frameworks=list(summary.get("frameworks", [])),
                entry_points=summary.get("entry_points", 0),
                functions=summary.get("functions", 0),
                side_effects=dict(summary.get("side_effects", {})),
                flows=summary.get("flows", 0),
                risk=dict(summary.get("risk", {})),
            ),
            scan=ScanInfo(**data.get("scan", {})),
            modules=[_module_from_dict(m) for m in data.get("modules", [])],
            entry_points=[
                EntryPoint(**{**e, "params": [Param(**p) for p in e.get("params", [])]})
                for e in data.get("entry_points", [])
            ],
            graph=_graph_from_dict(data.get("graph", {})),
            flows=[_flow_from_dict(f) for f in data.get("flows", [])],
            tools=[ToolProposal(**t) for t in data.get("tools", [])],
            orchestration=_orchestration_from_dict(data.get("orchestration", {})),
            multimodal=[MultimodalOpportunity(**m) for m in data.get("multimodal", [])],
            roadmap=Roadmap(phases=[Phase(**ph) for ph in data.get("roadmap", {}).get("phases", [])]),
            meta=dict(data.get("meta", {})),
            schema_version=version,
        )


def _orchestration_from_dict(o: dict[str, Any]) -> Orchestration:
    coord = o.get("coordinator")
    return Orchestration(
        coordinator=Agent(**coord) if coord else None,
        agents=[Agent(**a) for a in o.get("agents", [])],
        approvals=[ApprovalPoint(**ap) for ap in o.get("approvals", [])],
        notes=o.get("notes", ""),
    )


def _flow_from_dict(f: dict[str, Any]) -> Flow:
    return Flow(**{
        **f,
        "steps": [FlowStep(**st) for st in f.get("steps", [])],
        "resources": [FlowResource(**r) for r in f.get("resources", [])],
        "reasons": [RiskReason(**r) for r in f.get("reasons", [])],
    })


def _graph_from_dict(g: dict[str, Any]) -> Graph:
    return Graph(
        call_edges=[CallEdge(**e) for e in g.get("call_edges", [])],
        module_edges=[ModuleEdge(**e) for e in g.get("module_edges", [])],
        resources=[Resource(**r) for r in g.get("resources", [])],
        data_edges=[DataEdge(**e) for e in g.get("data_edges", [])],
        stats=dict(g.get("stats", {})),
    )


def _module_from_dict(m: dict[str, Any]) -> Module:
    return Module(
        path=m["path"],
        language=m["language"],
        name=m["name"],
        lines=m["lines"],
        imports=dict(m.get("imports", {})),
        exports=dict(m.get("exports", {})),
        functions=[
            Function(
                **{
                    **f,
                    "params": [Param(**p) for p in f.get("params", [])],
                    "side_effects": [SideEffect(**s) for s in f.get("side_effects", [])],
                    "method_branches": [MethodBranch(**b) for b in f.get("method_branches", [])],
                }
            )
            for f in m.get("functions", [])
        ],
        classes=[Class(**c) for c in m.get("classes", [])],
        parse_error=m.get("parse_error"),
    )


def save(analysis: Analysis, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(analysis.to_json(), encoding="utf-8")
    return out


def load(path: str | Path) -> Analysis:
    return Analysis.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

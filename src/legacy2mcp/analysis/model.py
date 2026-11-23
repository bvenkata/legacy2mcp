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

# Entry-point kinds the flow tracer (M3) starts from.
ENTRY_POINT_KINDS = ("http_route", "cli_command", "scheduled_job", "message_handler")


@dataclass
class SideEffect:
    kind: str  # one of SIDE_EFFECT_KINDS
    op: str  # "read" | "write" | "unknown"
    call: str  # the call expression as written, e.g. "stripe.Charge.create"
    line: int
    detail: str = ""  # e.g. the model/table name, URL literal, SQL verb


@dataclass
class Param:
    name: str
    type: str | None = None  # annotation as written, if any
    required: bool = True
    # Where a caller supplies it, when known: "path" | "query" | "body" | "form".
    # None for a plain function parameter that isn't an HTTP input.
    location: str | None = None


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


@dataclass
class Summary:
    files: int = 0
    lines: int = 0
    languages: dict[str, LanguageStats] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    entry_points: int = 0
    functions: int = 0
    side_effects: dict[str, int] = field(default_factory=dict)  # kind -> count


@dataclass
class Analysis:
    project: str  # basename of the scanned root
    summary: Summary = field(default_factory=Summary)
    scan: ScanInfo = field(default_factory=ScanInfo)
    modules: list[Module] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)
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
            ),
            scan=ScanInfo(**data.get("scan", {})),
            modules=[_module_from_dict(m) for m in data.get("modules", [])],
            entry_points=[
                EntryPoint(**{**e, "params": [Param(**p) for p in e.get("params", [])]})
                for e in data.get("entry_points", [])
            ],
            meta=dict(data.get("meta", {})),
            schema_version=version,
        )


def _module_from_dict(m: dict[str, Any]) -> Module:
    return Module(
        path=m["path"],
        language=m["language"],
        name=m["name"],
        lines=m["lines"],
        imports=dict(m.get("imports", {})),
        functions=[
            Function(
                **{
                    **f,
                    "params": [Param(**p) for p in f.get("params", [])],
                    "side_effects": [SideEffect(**s) for s in f.get("side_effects", [])],
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

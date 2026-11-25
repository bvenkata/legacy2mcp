"""
`legacy2mcp analyze` pipeline: walk -> parse -> cross-module resolution.

Later milestones append stages (graph, flows, risk, AI enrichment, ...);
each one adds a section to the same `Analysis` object.
"""

from __future__ import annotations

import importlib.metadata
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from legacy2mcp.analysis import frameworks as fw_detect
from legacy2mcp.analysis.analyzers import Analyzer, FileResult, Mount, default_analyzers
from legacy2mcp.analysis.analyzers.python import join_route
from legacy2mcp.analysis.flows import trace_flows
from legacy2mcp.analysis.graph import Resolver, build_graph
from legacy2mcp.analysis.orchestration import build_orchestration, find_multimodal
from legacy2mcp.analysis.roadmap import build_roadmap
from legacy2mcp.analysis.tools import propose_tools
from legacy2mcp.analysis.model import RISK_LEVELS, Analysis, EntryPoint, LanguageStats, Module, Param, ScanInfo, Summary
from legacy2mcp.analysis.walk import walk

_HTTP_VERBS = ("get", "post", "put", "patch", "delete")


def run_analysis(
    root: str | Path,
    exclude: list[str] | None = None,
    include_tests: bool = False,
    analyzers: list[Analyzer] | None = None,
) -> Analysis:
    root_path = Path(root).resolve()
    walked = walk(root_path, exclude=exclude, include_tests=include_tests)
    by_language: dict[str, Analyzer] = {}
    for analyzer in analyzers or default_analyzers():
        for lang in analyzer.languages:
            by_language[lang] = analyzer

    results: list[FileResult] = []
    unparsed: dict[str, int] = {}
    for source in walked.files:
        try:
            text = source.path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # permissions, or deleted since the walk: count it, don't abort
            walked.excluded["unreadable"] = walked.excluded.get("unreadable", 0) + 1
            continue
        analyzer = by_language.get(source.language)
        if analyzer is None:
            # Counted in the summary (size, languages) but not parsed.
            unparsed[source.language] = unparsed.get(source.language, 0) + 1
            lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            results.append(FileResult(module=Module(path=source.rel, language=source.language,
                                                    name=source.rel.rsplit(".", 1)[0], lines=lines)))
            continue
        results.append(analyzer.analyze(source, text))

    notes = [
        f"{count} {lang} file(s) counted but not parsed: install the analyze extra "
        f"(pip install \"legacy2mcp[analyze]\") for tree-sitter support."
        for lang, count in sorted(unparsed.items())
    ]
    modules = [r.module for r in results]
    mounts = [m for r in results for m in r.mounts]
    entry_points = [e for r in results for e in r.entry_points]
    entry_points = _apply_mounts(entry_points, mounts, modules)
    resolver = Resolver(modules)
    entry_points = _resolve_handlers(entry_points, resolver)
    entry_points = [_locate_params(e) for e in entry_points]
    entry_points.sort(key=lambda e: (e.kind, e.path or "", e.method or "", e.name, e.file, e.line))

    frameworks = set(fw_detect.from_manifests(root_path))
    for r in results:
        frameworks |= r.frameworks

    analysis = Analysis(
        project=root_path.name,
        scan=ScanInfo(
            included_files=len(results),
            excluded=dict(sorted(walked.excluded.items())),
            ignore_files=walked.ignore_files,
            exclude_patterns=list(exclude or []),
            notes=notes,
        ),
        modules=modules,
        entry_points=entry_points,
        graph=build_graph(modules, resolver),
        meta={
            "legacy2mcp_version": _version(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ai": False,
        },
    )
    analysis.flows = trace_flows(analysis)
    analysis.tools = propose_tools(analysis)
    analysis.orchestration = build_orchestration(analysis)
    analysis.multimodal = find_multimodal(analysis)
    analysis.roadmap = build_roadmap(analysis)
    analysis.summary = summarize(analysis, frameworks)
    return analysis


def summarize(analysis: Analysis, frameworks: set[str]) -> Summary:
    languages: dict[str, LanguageStats] = {}
    effects: dict[str, int] = {}
    functions = 0
    for m in analysis.modules:
        stats = languages.setdefault(m.language, LanguageStats())
        stats.files += 1
        stats.lines += m.lines
        functions += len(m.functions)
        for f in m.functions:
            for e in f.side_effects:
                effects[e.kind] = effects.get(e.kind, 0) + 1
    return Summary(
        files=len(analysis.modules),
        lines=sum(m.lines for m in analysis.modules),
        languages=dict(sorted(languages.items())),
        frameworks=sorted(frameworks),
        entry_points=len(analysis.entry_points),
        functions=functions,
        side_effects=dict(sorted(effects.items())),
        flows=len(analysis.flows),
        risk={level: sum(1 for f in analysis.flows if f.risk == level) for level in RISK_LEVELS},
    )


def _version() -> str:
    try:
        return importlib.metadata.version("legacy2mcp")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


def _apply_mounts(entry_points: list[EntryPoint], mounts: list[Mount], modules: list[Module]) -> list[EntryPoint]:
    """Prefix routes declared in a mounted route table (Django include())."""
    if not mounts:
        return entry_points
    resolver = Resolver(modules)
    path_of = {name: m.path for name, m in resolver.by_name.items()}
    # file -> list of (prefix, parent file)
    parents: dict[str, list[tuple[str, str]]] = {}
    for mount in mounts:
        target_file = path_of.get(mount.target, mount.target)
        parents.setdefault(target_file, []).append((mount.prefix, mount.file))

    def prefixes_for(file: str, seen: frozenset[str] = frozenset()) -> list[str]:
        if file not in parents or file in seen:
            return [""]
        out = []
        for prefix, parent in parents[file]:
            for upper in prefixes_for(parent, seen | {file}):
                out.append(join_route(upper, prefix) if upper else prefix)
        return out

    result: list[EntryPoint] = []
    for ep in entry_points:
        if ep.kind != "http_route" or ep.file not in parents or ep.path is None:
            result.append(ep)
            continue
        for prefix in prefixes_for(ep.file):
            path = join_route(prefix, ep.path)
            if ep.framework == "express" and len(path) > 1:
                path = path.rstrip("/")  # Express treats "/api/orders/" and "/api/orders" alike
            result.append(replace(ep, path=path, name=f"{ep.method} {path}"))
    return result


def _resolve_handlers(entry_points: list[EntryPoint], resolver: Resolver) -> list[EntryPoint]:
    """Turn handler names into `<file>::<qualname>`, expand class-based views
    into one entry point per HTTP method, and narrow method "ANY" using what
    the view function visibly handles."""
    out: list[EntryPoint] = []
    for ep in entry_points:
        found_cls = resolver.cls(ep.handler) if ep.kind == "http_route" else None
        if found_cls is not None:
            mod, cls = found_cls
            verbs = [v for v in _HTTP_VERBS if v in cls.methods]
            for verb in verbs or ["get"]:
                method = verb.upper() if ep.method == "ANY" else ep.method
                key = f"{mod.path}::{cls.name}.{verb}"
                func = resolver.functions.get(key)
                out.append(replace(ep, handler=key if func else f"{mod.path}::{cls.name}", method=method,
                                   name=f"{method} {ep.path}", params=list(func[1].params) if func else ep.params))
            continue
        key = resolver.function(ep.handler)
        if key is not None:
            _, func = resolver.functions[key]
            # The function's params include request-derived inputs
            # (request.args.get(...)) that a decorator alone can't see.
            ep = replace(ep, handler=key, params=list(func.params))
            if ep.kind == "http_route" and not ep.middleware:
                ep = replace(ep, middleware=[d for d in func.decorators if d.split(".")[-1] not in _ROUTE_DECORATORS])
            if ep.method == "ANY" and func.http_methods:
                for method in func.http_methods:
                    out.append(replace(ep, method=method, name=f"{method} {ep.path}"))
                continue
        out.append(ep)
    return out


_ROUTE_DECORATORS = {"route", "get", "post", "put", "patch", "delete", "head", "options", "api_view"}
_JS_FRAMEWORKS = {"express", "nextjs"}
_JS_PLUMBING = {"req", "res", "request", "response", "next", "ctx", "context"}

_PRIMITIVES = {None, "str", "int", "float", "bool", "Optional[str]", "Optional[int]", "int | None", "str | None",
               "float | None", "bool | None", "list[str]", "list[int]"}
_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


def _locate_params(ep: EntryPoint) -> EntryPoint:
    """Give every HTTP route param a location, and make sure every `{x}` in
    the path is a required param even when the handler signature hides it
    (a class-based view's `**kwargs`, say)."""
    if ep.kind != "http_route" or ep.path is None:
        return ep
    path_names = _PATH_PARAM_RE.findall(ep.path)
    params = []
    for p in ep.params:
        if ep.framework in _JS_FRAMEWORKS and p.location is None:
            continue  # handler signature (req, res, { params }) is plumbing; inputs come from req.*
        if ep.method in {"GET", "HEAD", "DELETE"} and p.location in {"form", "body"}:
            continue  # read by another method's branch of the same view
        if p.name in path_names:
            p = replace(p, location="path", required=True)
        elif p.location is None:
            primitive = p.type in _PRIMITIVES
            p = replace(p, location="query" if primitive or ep.method in {"GET", "DELETE", "HEAD"} else "body")
        params.append(p)
    known = {p.name for p in params}
    params = [Param(name=n, required=True, location="path") for n in path_names if n not in known] + params
    return replace(ep, params=params)

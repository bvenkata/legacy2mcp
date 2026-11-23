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
from legacy2mcp.analysis.model import Analysis, EntryPoint, LanguageStats, Module, Param, ScanInfo, Summary
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
    for source in walked.files:
        text = source.path.read_text(encoding="utf-8", errors="replace")
        analyzer = by_language.get(source.language)
        if analyzer is None:
            # Counted in the summary (size, languages) but not parsed yet.
            lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            results.append(FileResult(module=Module(path=source.rel, language=source.language,
                                                    name=source.rel.rsplit(".", 1)[0], lines=lines)))
            continue
        results.append(analyzer.analyze(source, text))

    modules = [r.module for r in results]
    mounts = [m for r in results for m in r.mounts]
    entry_points = [e for r in results for e in r.entry_points]
    entry_points = _apply_mounts(entry_points, mounts, modules)
    entry_points = _resolve_handlers(entry_points, modules)
    entry_points = [_locate_params(e) for e in entry_points]
    entry_points.sort(key=lambda e: (e.kind, e.path or "", e.method or "", e.name, e.file, e.line))

    frameworks = set(fw_detect.from_manifests(root_path))
    for r in results:
        frameworks |= r.frameworks

    analysis = Analysis(
        project=root_path.name,
        scan=ScanInfo(
            included_files=len(walked.files),
            excluded=dict(sorted(walked.excluded.items())),
            ignore_files=walked.ignore_files,
            exclude_patterns=list(exclude or []),
        ),
        modules=modules,
        entry_points=entry_points,
        meta={
            "legacy2mcp_version": _version(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "root": str(root_path),
            "ai": False,
        },
    )
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
    path_of = {m.name: m.path for m in modules}
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
            result.append(replace(ep, path=path, name=f"{ep.method} {path}"))
    return result


def _resolve_handlers(entry_points: list[EntryPoint], modules: list[Module]) -> list[EntryPoint]:
    """Turn dotted handler names into `<file>::<qualname>`, expand class-based
    views into one entry point per HTTP method, and narrow method "ANY"
    using what the view function visibly handles."""
    functions: dict[str, tuple[str, object]] = {}
    classes: dict[str, tuple[str, object]] = {}
    for m in modules:
        for f in m.functions:
            functions[f"{m.path}::{f.qualname}"] = (m.path, f)
            if m.name:
                functions[f"{m.name}.{f.qualname}"] = (m.path, f)
        for c in m.classes:
            classes[f"{m.path}::{c.name}"] = (m.path, c)
            if m.name:
                classes[f"{m.name}.{c.name}"] = (m.path, c)

    out: list[EntryPoint] = []
    for ep in entry_points:
        if ep.handler in classes and ep.kind == "http_route":
            path, cls = classes[ep.handler]
            verbs = [v for v in _HTTP_VERBS if v in cls.methods]  # type: ignore[attr-defined]
            for verb in verbs or ["get"]:
                method = verb.upper() if ep.method == "ANY" else ep.method
                qual = f"{cls.name}.{verb}"  # type: ignore[attr-defined]
                handler = f"{path}::{qual}" if verb in cls.methods else f"{path}::{cls.name}"  # type: ignore[attr-defined]
                func = functions.get(handler, (None, None))[1]
                out.append(replace(ep, handler=handler, method=method, name=f"{method} {ep.path}",
                                   params=list(func.params) if func is not None and not ep.params else ep.params))  # type: ignore[attr-defined]
            continue
        if ep.handler in functions:
            path, func = functions[ep.handler]
            # The function's params include request-derived inputs
            # (request.args.get(...)) that a decorator alone can't see.
            ep = replace(ep, handler=f"{path}::{func.qualname}", params=list(func.params))  # type: ignore[attr-defined]
            if ep.method == "ANY" and func.http_methods:  # type: ignore[attr-defined]
                for method in func.http_methods:  # type: ignore[attr-defined]
                    out.append(replace(ep, method=method, name=f"{method} {ep.path}"))
                continue
        out.append(ep)
    return out


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

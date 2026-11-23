"""
Stage 3 of `legacy2mcp analyze`: build the call graph, the module
dependency graph and the data-access graph from the parsed modules.

- Call graph: function -> function edges for calls that resolve inside the
  repo. "async" edges mark work handed to a queue (Celery `.delay()`), which
  the flow tracer follows but labels differently.
- Module graph: module -> module edges from imports of repo modules.
- Data-access graph: function -> resource edges from each side effect, where
  a resource is a table/model, an external host, a payment provider, a queue,
  email, a file or auth state.
"""

from __future__ import annotations

from urllib.parse import urlparse

from legacy2mcp.analysis.graph.resolve import Resolver
from legacy2mcp.analysis.model import CallEdge, DataEdge, Graph, Module, ModuleEdge, Resource, SideEffect

_KIND_LABEL = {
    "db": "database", "http_out": "external HTTP", "email": "email", "payment": "payments",
    "queue": "queue", "file": "filesystem", "auth": "auth / credentials",
}


def resource_for(effect: SideEffect) -> Resource:
    kind, detail = effect.kind, effect.detail.strip()
    if kind == "http_out":
        if detail.startswith("/"):
            return Resource(id="http:self", kind=kind, label="own API (relative URL)")
        host = urlparse(detail).netloc if "://" in detail else ""
        key = host or "external"
        return Resource(id=f"http:{key}", kind=kind, label=host or _KIND_LABEL[kind])
    if kind in ("email", "auth"):
        return Resource(id=kind, kind=kind, label=_KIND_LABEL[kind])
    if kind == "file":
        return Resource(id=f"file:{detail}" if detail else "file", kind=kind, label=detail or _KIND_LABEL[kind])
    if detail:
        return Resource(id=f"{kind}:{detail}", kind=kind, label=detail)
    return Resource(id=kind, kind=kind, label=_KIND_LABEL.get(kind, kind))


def build_graph(modules: list[Module], resolver: Resolver | None = None) -> Graph:
    resolver = resolver or Resolver(modules)
    call_edges: set[tuple[str, str, str]] = set()
    data_edges: set[tuple[str, str, str]] = set()
    resources: dict[str, Resource] = {}
    module_edges: dict[tuple[str, str], set[str]] = {}
    unresolved = 0
    external: set[str] = set()

    for m in modules:
        for f in m.functions:
            source = f"{m.path}::{f.qualname}"
            for call in f.calls:
                target = resolver.function(call, m, f.qualname)
                if target is not None:
                    if target != source:
                        call_edges.add((source, target, "call"))
                    continue
                enqueued = resolver.async_target(call, m)
                if enqueued is not None:
                    call_edges.add((source, enqueued, "async"))
                    continue
                unresolved += 1
            for effect in f.side_effects:
                res = resource_for(effect)
                resources.setdefault(res.id, res)
                data_edges.add((source, res.id, effect.op))

        for alias, target in m.imports.items():
            found = resolver.split(target)
            if found is None:
                pkg = target.split(".")[0]
                if pkg and not pkg.startswith(("@/", "~/")):
                    external.add(pkg)
                continue
            mod, _ = found
            if mod.path != m.path:
                module_edges.setdefault((m.path, mod.path), set()).add(alias)

    return Graph(
        call_edges=[CallEdge(source=s, target=t, kind=k) for s, t, k in sorted(call_edges)],
        module_edges=[ModuleEdge(source=s, target=t, names=sorted(n)) for (s, t), n in sorted(module_edges.items())],
        resources=sorted(resources.values(), key=lambda r: r.id),
        data_edges=[DataEdge(function=f, resource=r, op=o) for f, r, o in sorted(data_edges)],
        stats={
            "internal_calls": len(call_edges),
            "unresolved_calls": unresolved,
            "external_packages": sorted(external),
        },
    )

from __future__ import annotations

from pathlib import Path

from legacy2mcp.analysis.graph import Resolver, resource_for
from legacy2mcp.analysis.model import Function, Module, SideEffect
from legacy2mcp.analysis.pipeline import run_analysis

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def _mod(path, name, language="python", functions=(), exports=None):
    return Module(path=path, language=language, name=name, lines=1,
                  functions=[Function(qualname=q, line=1, end_line=1) for q in functions],
                  exports=exports or {})


def test_resolver_python_and_js():
    modules = [
        _mod("shop/pay.py", "shop.pay", functions=["charge", "Gateway.send"]),
        _mod("src/lib/email.ts", "src/lib/email", "typescript", ["sendMail"], {"sendMail": "sendMail"}),
        _mod("src/routes/index.js", "src/routes/index", "javascript", ["router"], {"default": "router"}),
    ]
    r = Resolver(modules)
    assert r.function("shop.pay.charge") == "shop/pay.py::charge"
    assert r.function("shop.pay.Gateway.send") == "shop/pay.py::Gateway.send"
    assert r.function("charge", modules[0]) == "shop/pay.py::charge"
    assert r.function("self.send", modules[0], "Gateway.other") == "shop/pay.py::Gateway.send"
    assert r.function("@/lib/email.sendMail") == "src/lib/email.ts::sendMail"
    assert r.function("src/routes") == "src/routes/index.js::router"
    assert r.function("stripe.Charge.create") is None
    assert r.function("x().y") is None
    assert r.async_target("shop.pay.charge.delay") == "shop/pay.py::charge"


def test_resource_ids():
    def eff(kind, detail="", op="write"):
        return SideEffect(kind=kind, op=op, call="x", line=1, detail=detail)

    assert resource_for(eff("http_out", "https://api.example.com/v1/x")).id == "http:api.example.com"
    assert resource_for(eff("http_out", "/api/self")).id == "http:self"
    assert resource_for(eff("http_out", "")).id == "http:external"
    assert resource_for(eff("db", "orders")).id == "db:orders"
    assert resource_for(eff("db")).label == "database"
    assert resource_for(eff("email", "ignored")).id == "email"


def test_flask_graph_edges():
    g = run_analysis(FIXTURES / "flask_shop").graph
    edges = {(e.source, e.target, e.kind) for e in g.call_edges}
    assert ("shop/orders.py::checkout", "shop/services/payments.py::charge_card", "call") in edges
    assert ("shop/orders.py::create_order", "shop/tasks.py::sync_inventory", "async") in edges
    data = {(d.function, d.resource, d.op) for d in g.data_edges}
    assert ("shop/services/payments.py::charge_card", "payment:stripe", "write") in data
    assert {"shop/orders.py", "shop/services/payments.py"} <= {e.source for e in g.module_edges} | {e.target for e in g.module_edges}


def test_express_cross_module_graph():
    a = run_analysis(FIXTURES / "express_store")
    edges = {(e.source, e.target) for e in a.graph.call_edges}
    assert ("src/routes/orders.js::<POST /{id}/pay>", "src/services/payments.js::chargeOrder") in edges
    assert ("bin/cli.js::<cli refund>", "src/services/payments.js::refundOrder") in edges
    assert "stripe" in a.graph.stats["external_packages"]
    assert "Pool()" not in a.graph.stats["external_packages"]


def test_js_counted_but_not_parsed_without_tree_sitter(monkeypatch):
    from legacy2mcp.analysis.analyzers import javascript

    monkeypatch.setattr(javascript, "available", lambda: False)
    a = run_analysis(FIXTURES / "express_store")
    assert a.summary.languages["javascript"].files == 11
    assert a.entry_points == []
    assert a.scan.notes and "11 javascript file(s) counted but not parsed" in a.scan.notes[0]

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from legacy2mcp.analysis.analyzers import javascript
from legacy2mcp.analysis.model import RISK_LEVELS, EntryPoint, SideEffect
from legacy2mcp.analysis.pipeline import run_analysis
from legacy2mcp.analysis.risk import rules

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def _eff(kind, op, detail="", verb="x", call="lib.x"):
    return SideEffect(kind=kind, op=op, call=call, line=1, detail=detail, verb=verb)


@pytest.mark.parametrize(
    "effect, level",
    [
        (_eff("db", "read", "orders"), rules.READ_ONLY),
        (_eff("db", "write", "orders", "INSERT"), rules.LOW),
        (_eff("db", "write", "orders", "DELETE"), rules.HIGH),
        (_eff("db", "write", "Order", "destroy"), rules.HIGH),
        (_eff("db", "unknown"), rules.LOW),
        (_eff("payment", "write", "stripe"), rules.HIGH),
        (_eff("payment", "read", "stripe"), rules.READ_ONLY),
        (_eff("email", "write"), rules.HIGH),
        (_eff("http_out", "write", "https://x.example.com"), rules.HIGH),
        (_eff("http_out", "write", "/api/self"), rules.LOW),
        (_eff("http_out", "unknown", "https://x.example.com"), rules.LOW),
        (_eff("queue", "write"), rules.LOW),
        (_eff("file", "write", "out.csv", "open"), rules.LOW),
        (_eff("file", "write", "", "unlink"), rules.HIGH),
        (_eff("auth", "write"), rules.HIGH),
        (_eff("auth", "read"), rules.READ_ONLY),
    ],
)
def test_classify_effect(effect, level):
    assert rules.classify_effect(effect)[0] == level


def test_entry_rules_and_auth():
    delete = EntryPoint(kind="http_route", framework="flask", handler="a::b", file="a.py", line=1,
                        name="DELETE /x", method="DELETE", path="/x")
    assert [r.level for r in rules.entry_reasons(delete, rules.READ_ONLY, "b")] == [rules.HIGH]
    post = EntryPoint(kind="http_route", framework="flask", handler="a::b", file="a.py", line=1,
                      name="POST /x", method="POST", path="/x", middleware=["src/mw.requireAuth"])
    assert "no write was detected" in rules.entry_reasons(post, rules.READ_ONLY, "b")[0].text
    assert rules.entry_reasons(post, rules.LOW, "b") == []
    assert rules.is_authenticated(post, False) is True
    cli = EntryPoint(kind="cli_command", framework="click", handler="a::b", file="a.py", line=1, name="cli x")
    assert rules.is_authenticated(cli, False) is None
    named = rules.entry_reasons(cli, rules.READ_ONLY, "cancel_order")
    assert named and named[0].level == rules.LOW


def _flow(analysis, flow_id):
    return next(f for f in analysis.flows if f.id == flow_id)


def test_flask_checkout_flow_is_ordered_and_high_risk():
    a = run_analysis(FIXTURES / "flask_shop")
    flow = _flow(a, "post-api-orders-order-id-checkout")
    assert flow.name == "Charge the customer's card and mark the order paid"
    assert flow.risk == rules.HIGH
    assert flow.authenticated is False
    labels = [(s.depth, s.kind, s.label) for s in flow.steps]
    assert labels == [
        (0, "enter", "POST /api/orders/{order_id}/checkout -> checkout"),
        (0, "effect", "reads Order"),
        (1, "call", "calls charge_card"),
        (1, "effect", "charges/changes via stripe"),
        (0, "effect", "writes database"),
        (1, "call", "calls send_receipt"),
        (1, "effect", "sends email via email"),
    ]
    assert {r.resource: r.op for r in flow.resources} == {
        "db": "write", "db:Order": "read", "email": "write", "payment:stripe": "write",
    }
    assert flow.functions == [
        "shop/orders.py::checkout", "shop/services/notify.py::send_receipt", "shop/services/payments.py::charge_card",
    ]


def test_async_work_is_followed_and_marked_background():
    flow = _flow(run_analysis(FIXTURES / "flask_shop"), "post-api-orders")
    async_steps = [s for s in flow.steps if s.kind == "async"]
    assert [s.label for s in async_steps] == ["enqueues sync_inventory to run in the background"]
    assert any(s.label.endswith("(background)") and s.effect_kind == "http_out" for s in flow.steps)


def test_summary_counts_risk_levels():
    a = run_analysis(FIXTURES / "django_clinic")
    assert a.summary.flows == len(a.flows) == 7
    assert sum(a.summary.risk.values()) == 7
    assert set(a.summary.risk) == set(RISK_LEVELS)


def test_django_method_branches_keep_get_read_only():
    a = run_analysis(FIXTURES / "django_clinic")
    assert _flow(a, "get-api-appointments-pk").risk == rules.READ_ONLY
    assert _flow(a, "put-api-appointments-pk").risk == rules.LOW


@pytest.mark.skipif(not javascript.available(), reason="tree-sitter not installed")
def test_next_switch_branches_and_express_auth():
    nxt = run_analysis(FIXTURES / "nextjs_shop")
    assert _flow(nxt, "get-api-products-id").risk == rules.READ_ONLY
    assert _flow(nxt, "delete-api-products-id").risk == rules.HIGH
    exp = run_analysis(FIXTURES / "express_store")
    pay = _flow(exp, "post-api-orders-id-pay")
    assert pay.authenticated is True and pay.risk == rules.HIGH
    assert "src/middleware/auth.requireAuth" in pay.middleware


def test_recursion_is_cut_with_seen_steps(tmp_path):
    (tmp_path / "app.py").write_text(textwrap.dedent('''
        from flask import Flask
        app = Flask(__name__)

        def a():
            b()

        def b():
            a()

        @app.get("/loop")
        def loop():
            a()
    '''))
    analysis = run_analysis(tmp_path)
    flow = analysis.flows[0]
    assert [s.kind for s in flow.steps] == ["enter", "call", "call", "seen"]


def test_unresolved_handler_yields_empty_flow_with_note(tmp_path):
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom thirdparty import views\n"
        "urlpatterns = [path('hook/', views.webhook)]\n"
    )
    (flow,) = run_analysis(tmp_path).flows
    assert flow.steps == [] and flow.handler == "thirdparty.views.webhook"
    assert flow.reasons[0].level == "info" and "could not be resolved" in flow.reasons[0].text

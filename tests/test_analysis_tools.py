from __future__ import annotations

from pathlib import Path

from legacy2mcp.analysis.model import Param
from legacy2mcp.analysis.pipeline import run_analysis
from legacy2mcp.analysis.tools.propose import input_schema, json_type, tool_name

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def test_tool_names():
    used: set[str] = set()
    assert tool_name("GET", "/api/orders", used) == "list_orders"
    assert tool_name("GET", "/api/orders/{id}", used) == "get_orders_by_id"
    assert tool_name("POST", "/api/v1/invoices/{invoiceId}/refund", used) == "refund_invoices_by_invoice_id"
    assert tool_name("GET", "/health", used) == "get_health"
    assert tool_name("POST", "/api/auth/login", used) == "login_auth"
    assert tool_name("POST", "/api/checkout", used) == "checkout"
    assert tool_name("GET", "/api/orders", used) == "list_orders_2"


def test_json_types_and_schema():
    assert json_type("int") == {"type": "integer"}
    assert json_type("int | None") == {"type": "integer"}
    assert json_type("Optional[str]") == {"type": "string"}
    assert json_type("list[str]") == {"type": "array"}
    assert json_type("string[]") == {"type": "array"}
    assert json_type("InvoiceIn")["type"] == "object"
    schema = input_schema([Param("id", "int", True, "path"), Param("q", None, False, "query"), Param("{ params }")])
    assert schema == {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "URL path parameter"},
            "q": {"type": "string", "description": "query-string parameter"},
        },
        "additionalProperties": False,
        "required": ["id"],
    }


def test_writes_start_excluded_and_reads_included():
    tools = {t.name: t for t in run_analysis(FIXTURES / "flask_shop").tools}
    assert tools["get_orders_by_order_id"].include and tools["get_orders_by_order_id"].safe
    checkout = tools["checkout_orders_by_order_id"]
    assert checkout.risk == "high_risk" and not checkout.include and not checkout.safe
    assert "no authentication detected on this write" in checkout.notes
    assert checkout.input_schema["required"] == ["order_id", "card_token"]


def test_non_http_flows_get_no_tools_and_any_method_is_not_exposable(tmp_path):
    a = run_analysis(FIXTURES / "express_store")
    assert {t.flow_id for t in a.tools} <= {f.id for f in a.flows if f.entry_kind == "http_route"}
    (tmp_path / "urls.py").write_text("from django.urls import path\nfrom ext import v\nurlpatterns = [path('x/', v.view)]\n")
    (tool,) = run_analysis(tmp_path).tools
    assert not tool.exposable and not tool.include

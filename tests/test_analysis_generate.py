from __future__ import annotations

import json
import socket
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
import yaml

from legacy2mcp import cli
from legacy2mcp.analysis.generate import GenerateError, build_config, load_selections
from legacy2mcp.analysis.pipeline import run_analysis
from legacy2mcp.config import Legacy2McpConfig

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def _endpoints(result):
    return {e["name"]: e for e in result.config["adapters"][0]["config"]["endpoints"]}


def test_default_selection_is_read_only_and_valid():
    a = run_analysis(FIXTURES / "flask_shop")
    result = build_config(a, base_url="http://localhost:5000")
    eps = _endpoints(result)
    assert set(eps) == {t.name for t in a.tools if t.include}
    assert all(not e["write"] for e in eps.values())
    cfg = result.config["adapters"][0]["config"]
    assert cfg["allow_write_operations"] is False and cfg["base_url"] == "http://localhost:5000"
    assert eps["get_orders_by_order_id"]["params"] == [{"name": "order_id", "location": "path", "type": "integer", "required": True}]
    Legacy2McpConfig.model_validate(result.config)


def test_explicit_flows_writes_stay_hidden_unless_allowed():
    a = run_analysis(FIXTURES / "flask_shop")
    result = build_config(a, flows=["post-api-orders-order-id-checkout", "task-sync-inventory", "nope"])
    eps = _endpoints(result)
    assert list(eps) == ["checkout_orders_by_order_id"]
    ep = eps["checkout_orders_by_order_id"]
    assert ep["write"] is True and ep["needs_review"] is True and "HIGH-RISK" in ep["description"]
    assert result.config["adapters"][0]["config"]["allow_write_operations"] is False
    assert any("hidden until you set allow_write_operations" in t for t in result.todos)
    assert any("human approval" in t for t in result.todos)
    assert any("Set base_url" in t for t in result.todos)
    assert sorted(s.split(":")[0] for s in result.skipped) == ["nope", "task-sync-inventory"]
    allowed = build_config(a, flows=["post-api-orders-order-id-checkout"], allow_writes=True, token_env="APP_TOKEN")
    cfg = allowed.config["adapters"][0]["config"]
    assert cfg["allow_write_operations"] is True and cfg["auth"] == {"type": "bearer", "token_env": "APP_TOKEN"}


def test_selections_file_round_trip(tmp_path):
    a = run_analysis(FIXTURES / "express_store")
    sel = {"schema": "legacy2mcp-selections/1", "tools": [
        {"name": t.name, "flow_id": t.flow_id, "include": t.name in ("list_orders", "create_orders")} for t in a.tools]}
    path = tmp_path / "selections.json"
    path.write_text(json.dumps(sel))
    result = build_config(a, selections=load_selections(path))
    assert set(_endpoints(result)) == {"list_orders", "create_orders"}
    # a tool renamed after export (e.g. by AI enrichment) still matches by flow id
    next(t for t in a.tools if t.name == "list_orders").name = "renamed_by_ai"
    assert set(_endpoints(build_config(a, selections=load_selections(path)))) == {"renamed_by_ai", "create_orders"}


def test_bad_selections_and_empty_selection(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"tools": []}')
    with pytest.raises(GenerateError, match="legacy2mcp-selections/1"):
        load_selections(bad)
    a = run_analysis(FIXTURES / "flask_shop")
    with pytest.raises(GenerateError, match="no tools selected"):
        build_config(a, selections={})


def test_structured_and_form_params_become_todos():
    fb = run_analysis(FIXTURES / "fastapi_billing")
    result = build_config(fb, flows=["post-invoices"])
    assert _endpoints(result)["create_invoices"]["params"] == []
    assert any("structured object (InvoiceIn)" in t for t in result.todos)
    dj = run_analysis(FIXTURES / "django_clinic")
    result = build_config(dj, flows=["post-api-appointments-pk-pay"])
    assert {p["name"]: p["location"] for p in _endpoints(result)["pay_appointments_by_pk"]["params"]} == {"pk": "path", "nonce": "body"}
    assert any("form field" in t for t in result.todos)


# ---------------------------------------------------------------- end to end
APP = textwrap.dedent('''
    from flask import Flask, jsonify, request

    app = Flask(__name__)
    ITEMS = {1: {"id": 1, "name": "widget", "stock": 5}}


    @app.get("/api/items/<int:item_id>")
    def get_item(item_id: int):
        """Fetch one item."""
        return jsonify(ITEMS[item_id])


    @app.post("/api/items/<int:item_id>/restock")
    def restock(item_id: int):
        """Add stock to an item."""
        qty = request.get_json()["qty"]
        ITEMS[item_id]["stock"] += qty
        db.session.commit()
        return jsonify(ITEMS[item_id])
''')


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_app(tmp_path):
    src = tmp_path / "app"
    src.mkdir()
    (src / "app.py").write_text(APP)
    namespace: dict = {"__name__": "__main__", "db": type("DB", (), {"session": type("S", (), {"commit": staticmethod(lambda: None)})})}
    exec(compile(APP, "app.py", "exec"), namespace)
    port = _free_port()
    threading.Thread(target=lambda: namespace["app"].run(host="127.0.0.1", port=port, use_reloader=False), daemon=True).start()
    time.sleep(0.8)
    return src, f"http://127.0.0.1:{port}"


async def test_analyze_generate_run_end_to_end(live_app, tmp_path, monkeypatch):
    from legacy2mcp.server import Legacy2McpApp

    src, base_url = live_app
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "analyze", str(src), "--no-ai", "--out", str(out)])
    cli.main()
    analysis = json.loads((out / "analysis.json").read_text())
    restock = next(t for t in analysis["tools"] if t["path"] == "/api/items/{item_id}/restock")
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "generate", str(out), "--flows",
                                      f"get-api-items-item-id,{restock['flow_id']}", "--base-url", base_url,
                                      "--out", str(tmp_path / "config.yaml")])
    cli.main()
    config = yaml.safe_load((tmp_path / "config.yaml").read_text())
    config["security"]["audit"]["path"] = str(tmp_path / "audit.log")

    # Writes stay hidden by default...
    app = Legacy2McpApp(Legacy2McpConfig.model_validate(config))
    names = set(app._tools)
    assert "app_get_items_by_item_id" in names and not any("restock" in n for n in names)
    content = await app._dispatch("app_get_items_by_item_id", {"item_id": 1})
    assert json.loads(content[0].text)["name"] == "widget"
    with pytest.raises(ValueError):  # schema validation rejects a bad argument before any HTTP call
        await app._dispatch("app_get_items_by_item_id", {"item_id": "one"})
    app.close()

    # ...and appear only when explicitly allowed. The body field the analyzer
    # couldn't see (request.get_json()["qty"]) is added by hand, as the TODO says.
    config["adapters"][0]["config"]["allow_write_operations"] = True
    ep = next(e for e in config["adapters"][0]["config"]["endpoints"] if "restock" in e["name"])
    ep["params"].append({"name": "qty", "location": "body", "type": "integer", "required": True})
    app = Legacy2McpApp(Legacy2McpConfig.model_validate(config))
    restock_tool = next(n for n in app._tools if "restock" in n)
    result = await app._dispatch(restock_tool, {"item_id": 1, "qty": 3})
    assert json.loads(result[0].text)["stock"] == 8
    app.close()
    log = [json.loads(line) for line in (tmp_path / "audit.log").read_text().splitlines()]
    assert {e["tool"] for e in log} >= {"app_get_items_by_item_id", restock_tool}

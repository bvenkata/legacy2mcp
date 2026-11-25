"""Demo "Orders" SOAP service on http://127.0.0.1:8124/orders

Serves examples/soap/orders.wsdl and answers its read operations (GetOrder,
ListOrders, GetInventory) from a small in-memory dataset. The write operations
(CreateOrder, CancelOrder) are implemented too, so you can see legacy2mcp hide
them by default and expose them only when you set `allow_write_operations: true`.

This is a fixed demo fixture, not a general-purpose SOAP server.

    pip install "legacy2mcp[dev]"          # needs flask + lxml
    python examples/soap/mock_orders_service.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from flask import Flask, Response, request
from lxml import etree

NS = "http://legacy2mcp.example/orders"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSDL_PATH = Path(__file__).with_name("orders.wsdl")
DEFAULT_ENDPOINT = "http://127.0.0.1:8124/orders"  # soap:address baked into orders.wsdl

INVENTORY = {"WIDGET-1": 120, "WIDGET-2": 0, "GADGET-9": 37}
ORDERS = {
    1001: {"customer": "Acme Corp", "status": "shipped", "items": [("WIDGET-1", 10), ("GADGET-9", 2)]},
    1002: {"customer": "Globex", "status": "pending", "items": [("WIDGET-2", 5)]},
    1003: {"customer": "Initech", "status": "delivered", "items": [("GADGET-9", 1)]},
}
UNIT_PRICE = {"WIDGET-1": Decimal("4.50"), "WIDGET-2": Decimal("6.00"), "GADGET-9": Decimal("19.99")}


def _total(items) -> Decimal:
    return sum((UNIT_PRICE.get(sku, Decimal("0")) * qty for sku, qty in items), Decimal("0"))


def _q(name: str) -> str:
    return f"{{{NS}}}{name}"


def _text(parent, name: str) -> str | None:
    el = parent.find(_q(name))
    return None if el is None else (el.text or "").strip()


def _order_xml(parent, tag: str, order_id: int, order: dict) -> None:
    el = etree.SubElement(parent, _q(tag))
    etree.SubElement(el, _q("orderId")).text = str(order_id)
    etree.SubElement(el, _q("customer")).text = order["customer"]
    etree.SubElement(el, _q("status")).text = order["status"]
    etree.SubElement(el, _q("total")).text = str(_total(order["items"]))
    for sku, qty in order["items"]:
        item = etree.SubElement(el, _q("items"))
        etree.SubElement(item, _q("sku")).text = sku
        etree.SubElement(item, _q("qty")).text = str(qty)


def _envelope(response_tag: str):
    env = etree.Element(f"{{{SOAP_NS}}}Envelope", nsmap={"soap": SOAP_NS})
    body = etree.SubElement(env, f"{{{SOAP_NS}}}Body")
    resp = etree.SubElement(body, _q(response_tag), nsmap={None: NS})
    return env, resp


def _fault(message: str) -> Response:
    xml = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body><soap:Fault>'
        f"<faultcode>Client</faultcode><faultstring>{message}</faultstring>"
        f"</soap:Fault></soap:Body></soap:Envelope>"
    )
    return Response(xml, mimetype="text/xml", status=500)


def create_app() -> Flask:
    app = Flask(__name__)
    wsdl_xml = WSDL_PATH.read_text(encoding="utf-8")
    state_orders = {k: dict(v, items=list(v["items"])) for k, v in ORDERS.items()}

    @app.get("/orders")
    def wsdl():
        if request.args.get("wsdl") is not None or request.args.get("WSDL") is not None:
            # Point the WSDL's endpoint at wherever this service is actually running.
            served = wsdl_xml.replace(DEFAULT_ENDPOINT, request.url_root.rstrip("/") + "/orders")
            return Response(served, mimetype="text/xml")
        return Response("Orders SOAP endpoint. Append ?wsdl to GET.", mimetype="text/plain")

    @app.post("/orders")
    def call():
        root = etree.fromstring(
            request.get_data(), etree.XMLParser(resolve_entities=False, no_network=True)
        )
        op_el = list(root.find(f"{{{SOAP_NS}}}Body"))[0]
        op = etree.QName(op_el.tag).localname

        if op == "GetOrder":
            order_id = int(_text(op_el, "orderId"))
            if order_id not in state_orders:
                return _fault(f"Order {order_id} not found")
            env, resp = _envelope("GetOrderResponse")
            _order_xml(resp, "order", order_id, state_orders[order_id])

        elif op == "ListOrders":
            status = _text(op_el, "status")
            env, resp = _envelope("ListOrdersResponse")
            for order_id, order in sorted(state_orders.items()):
                if not status or order["status"] == status:
                    _order_xml(resp, "orders", order_id, order)

        elif op == "GetInventory":
            sku = _text(op_el, "sku")
            if sku not in INVENTORY:
                return _fault(f"Unknown SKU {sku}")
            env, resp = _envelope("GetInventoryResponse")
            etree.SubElement(resp, _q("sku")).text = sku
            etree.SubElement(resp, _q("onHand")).text = str(INVENTORY[sku])

        elif op == "CreateOrder":
            new_id = max(state_orders) + 1
            state_orders[new_id] = {
                "customer": _text(op_el, "customer"),
                "status": "pending",
                "items": [(_text(op_el, "sku"), int(_text(op_el, "qty")))],
            }
            env, resp = _envelope("CreateOrderResponse")
            etree.SubElement(resp, _q("orderId")).text = str(new_id)

        elif op == "CancelOrder":
            order_id = int(_text(op_el, "orderId"))
            order = state_orders.get(order_id)
            cancelled = bool(order) and order["status"] in ("pending",)
            if cancelled:
                order["status"] = "cancelled"
            env, resp = _envelope("CancelOrderResponse")
            etree.SubElement(resp, _q("cancelled")).text = "true" if cancelled else "false"

        else:
            return _fault(f"Unknown operation {op}")

        return Response(etree.tostring(env, xml_declaration=True, encoding="utf-8"), mimetype="text/xml")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8124)

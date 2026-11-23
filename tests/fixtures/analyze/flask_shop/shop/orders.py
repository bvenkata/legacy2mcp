from flask import Blueprint, request

from shop.models import Order, db
from shop.services.notify import send_receipt
from shop.services.payments import charge_card
from shop.tasks import sync_inventory

bp = Blueprint("orders", __name__, url_prefix="/api/orders")


@bp.get("/")
def list_orders():
    """List recent orders."""
    status = request.args.get("status")
    return [o.id for o in Order.query.filter_by(status=status).all()]


@bp.get("/<int:order_id>")
def get_order(order_id: int):
    """Fetch one order by id."""
    return Order.query.get_or_404(order_id)


@bp.route("/", methods=["POST"])
def create_order():
    """Create a pending order."""
    payload = request.get_json()
    order = Order(customer_email=payload["email"], total=payload["total"])
    db.session.add(order)
    db.session.commit()
    sync_inventory.delay(order.id)
    return {"id": order.id}


@bp.post("/<int:order_id>/checkout")
def checkout(order_id: int, card_token: str):
    """Charge the customer's card and mark the order paid."""
    order = Order.query.get_or_404(order_id)
    charge_card(order, card_token)
    order.status = "paid"
    db.session.commit()
    send_receipt(order)
    return {"status": order.status}


@bp.delete("/<int:order_id>")
def delete_order(order_id: int):
    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    return {}

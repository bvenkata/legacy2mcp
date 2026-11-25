import httpx
import stripe
from fastapi import APIRouter, Depends

from app.db import get_session
from app.models import InvoiceIn, RefundIn

router = APIRouter(prefix="/invoices")


@router.get("/{invoice_id}")
def read_invoice(invoice_id: int, session=Depends(get_session)):
    """Return one invoice."""
    return session.execute("SELECT * FROM invoices WHERE id = :id", {"id": invoice_id}).first()


@router.get("/")
def search_invoices(customer_id: int | None = None, limit: int = 50, session=Depends(get_session)):
    return session.query("Invoice").limit(limit).all()


@router.post("/")
def create_invoice(body: InvoiceIn, session=Depends(get_session)):
    """Create an invoice and register it with the tax service."""
    session.execute("INSERT INTO invoices (customer_id, amount) VALUES (:c, :a)", body.dict())
    session.commit()
    httpx.post("https://tax.example.com/v1/register", json=body.dict())
    return {"ok": True}


@router.post("/{invoice_id}/refund")
async def refund_invoice(invoice_id: int, body: RefundIn, session=Depends(get_session)):
    """Refund a paid invoice to the original card."""
    row = session.execute("SELECT charge_id FROM invoices WHERE id = :id", {"id": invoice_id}).first()
    stripe.Refund.create(charge=row.charge_id)
    session.execute("UPDATE invoices SET status = 'refunded' WHERE id = :id", {"id": invoice_id})
    session.commit()
    return {"refunded": True}

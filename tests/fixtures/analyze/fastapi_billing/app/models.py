from pydantic import BaseModel


class InvoiceIn(BaseModel):
    customer_id: int
    amount: float


class RefundIn(BaseModel):
    reason: str

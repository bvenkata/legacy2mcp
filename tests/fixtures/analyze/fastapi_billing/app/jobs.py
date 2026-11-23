from app.db import SessionLocal
from app.mailer import send_email


def send_overdue_reminders():
    """Email every customer with an overdue invoice."""
    session = SessionLocal()
    rows = session.execute("SELECT email FROM invoices WHERE status = 'overdue'").all()
    for row in rows:
        send_email(row.email, "Invoice overdue", "Please pay.")

import smtplib
from email.message import EmailMessage


def send_receipt(order):
    msg = EmailMessage()
    msg["To"] = order.customer_email
    msg.set_content(f"Thanks for order {order.id}")
    with smtplib.SMTP("localhost") as smtp:
        smtp.send_message(msg)

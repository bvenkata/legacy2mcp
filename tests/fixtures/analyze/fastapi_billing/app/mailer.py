import smtplib


def send_email(to, subject, body):
    with smtplib.SMTP("localhost") as smtp:
        smtp.sendmail("billing@example.com", [to], f"Subject: {subject}\n\n{body}")

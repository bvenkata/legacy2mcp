from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Order(db.Model):
    """A customer order."""

    id = db.Column(db.Integer, primary_key=True)
    customer_email = db.Column(db.String)
    total = db.Column(db.Numeric)
    status = db.Column(db.String, default="pending")

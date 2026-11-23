from flask import Flask

from shop.models import db
from shop.orders import bp as orders_bp


def create_app():
    app = Flask(__name__)
    db.init_app(app)
    app.register_blueprint(orders_bp)
    return app

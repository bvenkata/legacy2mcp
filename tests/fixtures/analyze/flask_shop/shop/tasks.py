import requests
from celery import Celery

celery = Celery("shop")


@celery.task
def sync_inventory(order_id):
    """Push the order's items to the warehouse system."""
    requests.post("https://warehouse.internal/api/reservations", json={"order": order_id})

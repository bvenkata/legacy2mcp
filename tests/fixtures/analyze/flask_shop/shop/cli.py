import click

from shop.models import Order


@click.group()
def cli():
    pass


@cli.command("export-orders")
def export_orders():
    """Write all orders to a CSV file."""
    with open("orders.csv", "w") as fh:
        for order in Order.query.all():
            fh.write(f"{order.id},{order.total}\n")

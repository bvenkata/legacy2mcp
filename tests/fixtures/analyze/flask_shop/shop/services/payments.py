import stripe


def charge_card(order, card_token):
    """Charge the order total to a tokenized card."""
    return stripe.Charge.create(amount=int(order.total * 100), currency="usd", source=card_token)


def refund(charge_id):
    return stripe.Refund.create(charge=charge_id)

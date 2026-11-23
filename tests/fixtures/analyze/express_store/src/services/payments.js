const Stripe = require("stripe");
const axios = require("axios");
const db = require("../db");

const stripe = new Stripe(process.env.STRIPE_KEY);

/** Charge the order total and hand the order to the warehouse. */
async function chargeOrder(orderId, paymentMethodId) {
  const { rows } = await db.query("SELECT total FROM orders WHERE id = $1", [orderId]);
  const intent = await stripe.paymentIntents.create({
    amount: rows[0].total,
    currency: "usd",
    payment_method: paymentMethodId,
    confirm: true,
  });
  await db.query("UPDATE orders SET status = 'paid' WHERE id = $1", [orderId]);
  await notifyWarehouse(orderId);
  return intent;
}

async function refundOrder(orderId) {
  const { rows } = await db.query("SELECT payment_intent FROM orders WHERE id = $1", [orderId]);
  await stripe.refunds.create({ payment_intent: rows[0].payment_intent });
}

async function notifyWarehouse(orderId) {
  await axios.post("https://warehouse.example.com/v1/shipments", { orderId });
}

module.exports = { chargeOrder, refundOrder };

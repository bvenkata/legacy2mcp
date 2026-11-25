const db = require("../db");
const { emailQueue } = require("../queues");

/** List orders, optionally filtered by status. */
async function list(req, res) {
  const { status } = req.query;
  const { rows } = await db.query("SELECT * FROM orders WHERE status = $1", [status]);
  res.json(rows);
}

async function get(req, res) {
  const { rows } = await db.query("SELECT * FROM orders WHERE id = $1", [req.params.id]);
  res.json(rows[0]);
}

/** Create an order and queue a confirmation email. */
async function create(req, res) {
  const { items, email } = req.body;
  const { rows } = await db.query("INSERT INTO orders (items, email) VALUES ($1, $2) RETURNING id", [items, email]);
  await emailQueue.add("order-confirmation", { orderId: rows[0].id, email });
  res.status(201).json(rows[0]);
}

async function remove(req, res) {
  await db.query("DELETE FROM orders WHERE id = $1", [req.params.id]);
  res.status(204).end();
}

module.exports = { list, get, create, remove };

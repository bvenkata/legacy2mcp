const express = require("express");
const { requireAuth } = require("../middleware/auth");
const orders = require("../controllers/orders");
const payments = require("../services/payments");

const router = express.Router();

router.get("/", requireAuth, orders.list);
router.get("/:id", requireAuth, orders.get);
router.post("/", requireAuth, orders.create);

router.post("/:id/pay", requireAuth, async (req, res) => {
  const { paymentMethodId } = req.body;
  const intent = await payments.chargeOrder(req.params.id, paymentMethodId);
  res.json({ status: intent.status });
});

router.route("/:id").delete(requireAuth, orders.remove);

module.exports = router;

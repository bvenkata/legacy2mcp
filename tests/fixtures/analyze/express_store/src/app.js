const express = require("express");
const ordersRouter = require("./routes/orders");
const authRouter = require("./routes/auth");

const app = express();
app.use(express.json());
app.use("/api/orders", ordersRouter);
app.use("/api/auth", authRouter);

app.get("/health", (req, res) => res.json({ ok: true }));

module.exports = app;

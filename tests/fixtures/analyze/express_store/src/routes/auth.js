const express = require("express");
const bcrypt = require("bcrypt");
const jwt = require("jsonwebtoken");
const db = require("../db");

const router = express.Router();

router.post("/login", async (req, res) => {
  const { email, password } = req.body;
  const { rows } = await db.query("SELECT id, password_hash FROM users WHERE email = $1", [email]);
  const ok = rows.length && (await bcrypt.compare(password, rows[0].password_hash));
  if (!ok) return res.status(401).end();
  res.json({ token: jwt.sign({ sub: rows[0].id }, process.env.JWT_SECRET) });
});

module.exports = router;

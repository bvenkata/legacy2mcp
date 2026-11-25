const jwt = require("jsonwebtoken");

function requireAuth(req, res, next) {
  const token = (req.headers.authorization || "").replace("Bearer ", "");
  req.user = jwt.verify(token, process.env.JWT_SECRET);
  next();
}

module.exports = { requireAuth };

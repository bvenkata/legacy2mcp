const cron = require("node-cron");
const db = require("../db");

cron.schedule("0 3 * * *", async () => {
  await db.query("DELETE FROM carts WHERE updated_at < now() - interval '7 days'");
});

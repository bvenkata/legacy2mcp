#!/usr/bin/env node
const { program } = require("commander");
const { refundOrder } = require("../src/services/payments");

program
  .command("refund <orderId>")
  .description("Refund an order to the original card")
  .action(async (orderId) => {
    await refundOrder(orderId);
  });

program.parse();

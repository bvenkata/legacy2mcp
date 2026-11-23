const { Worker } = require("bullmq");
const nodemailer = require("nodemailer");

const transporter = nodemailer.createTransport({ host: "localhost", port: 25 });

new Worker("emails", async (job) => {
  await transporter.sendMail({ to: job.data.email, subject: "Your order", text: `Order ${job.data.orderId}` });
});

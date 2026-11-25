const { Queue } = require("bullmq");

const emailQueue = new Queue("emails");

module.exports = { emailQueue };

import { Resend } from "resend";

const resend = new Resend(process.env.RESEND_API_KEY);

/** Send the order confirmation email. */
export async function sendOrderEmail(to: string, orderId: string): Promise<void> {
  await resend.emails.send({ from: "shop@example.com", to, subject: "Order confirmed", text: `Order ${orderId}` });
}

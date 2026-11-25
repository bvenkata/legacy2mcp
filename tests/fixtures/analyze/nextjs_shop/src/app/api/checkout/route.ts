import { NextRequest, NextResponse } from "next/server";
import Stripe from "stripe";
import { prisma } from "@/lib/prisma";
import { sendOrderEmail } from "@/lib/email";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

/** Turn a cart into a paid order. */
export async function POST(request: NextRequest) {
  const { cartId, email } = await request.json();
  const cart = await prisma.cart.findUnique({ where: { id: cartId }, include: { items: true } });
  const session = await stripe.checkout.sessions.create({ mode: "payment", line_items: [] });
  const order = await prisma.order.create({ data: { cartId, email, stripeSession: session.id } });
  await sendOrderEmail(email, order.id);
  return NextResponse.json({ url: session.url });
}

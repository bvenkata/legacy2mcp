import { NextResponse } from "next/server";
import Stripe from "stripe";
import { prisma } from "@/lib/prisma";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(request: Request, { params }: { params: { orderId: string } }) {
  const order = await prisma.order.findUnique({ where: { id: params.orderId } });
  await stripe.refunds.create({ payment_intent: order!.paymentIntent });
  await prisma.order.update({ where: { id: params.orderId }, data: { status: "refunded" } });
  return NextResponse.json({ refunded: true });
}

import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

/** Search the product catalog. */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q") ?? "";
  const products = await prisma.product.findMany({ where: { name: { contains: q } }, take: 50 });
  return NextResponse.json(products);
}

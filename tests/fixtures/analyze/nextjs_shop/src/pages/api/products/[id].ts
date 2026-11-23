import type { NextApiRequest, NextApiResponse } from "next";
import { prisma } from "@/lib/prisma";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const id = String(req.query.id);
  switch (req.method) {
    case "GET":
      return res.json(await prisma.product.findUnique({ where: { id } }));
    case "PUT":
      return res.json(await prisma.product.update({ where: { id }, data: req.body }));
    case "DELETE":
      await prisma.product.delete({ where: { id } });
      return res.status(204).end();
    default:
      return res.status(405).end();
  }
}

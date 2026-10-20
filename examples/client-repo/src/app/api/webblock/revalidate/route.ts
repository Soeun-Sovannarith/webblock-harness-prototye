// Called by the harness after a runtime-config publish (RUNTIME_REVALIDATE_URL_TEMPLATE).
import { revalidateTag } from "next/cache";

export async function POST(req: Request) {
  const auth = req.headers.get("authorization");
  if (!process.env.WEBBLOCK_REVALIDATE_SECRET || auth !== `Bearer ${process.env.WEBBLOCK_REVALIDATE_SECRET}`) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }
  revalidateTag("webblock-config");
  return Response.json({ revalidated: true });
}

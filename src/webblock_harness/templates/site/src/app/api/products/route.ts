import { createProduct, listProducts } from "../../../db/queries";
import { handle, json, readJson, refreshSite } from "../../../lib/api";
import { requireOwner } from "../../../lib/portal-auth";
import { productInput } from "../../../lib/validation";

export const dynamic = "force-dynamic";

/** Public: active products. With ?include=all (owner only): hidden products too. */
export const GET = handle(async (request: Request) => {
  const all = new URL(request.url).searchParams.get("include") === "all";
  if (all) await requireOwner(request);
  return json({ products: await listProducts(all) });
});

export const POST = handle(async (request: Request) => {
  await requireOwner(request);
  const product = await createProduct(productInput.parse(await readJson(request)));
  refreshSite();
  return json({ product }, 201);
});

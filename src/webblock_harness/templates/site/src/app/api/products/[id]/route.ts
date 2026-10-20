import { deleteProduct, updateProduct } from "../../../../db/queries";
import { handle, json, readJson, refreshSite, requireUuid } from "../../../../lib/api";
import { HttpError, requireOwner } from "../../../../lib/portal-auth";
import { productPatch } from "../../../../lib/validation";

type Ctx = { params: Promise<{ id: string }> };

export const PATCH = handle(async (request: Request, ctx: Ctx) => {
  await requireOwner(request);
  const id = requireUuid((await ctx.params).id);
  const product = await updateProduct(id, productPatch.parse(await readJson(request)));
  if (!product) throw new HttpError(404, "not_found", "Product not found.");
  refreshSite();
  return json({ product });
});

export const DELETE = handle(async (request: Request, ctx: Ctx) => {
  await requireOwner(request);
  if (!(await deleteProduct(requireUuid((await ctx.params).id)))) throw new HttpError(404, "not_found", "Product not found.");
  refreshSite();
  return json({ deleted: true });
});

import { deleteContact, updateContact } from "../../../../../db/queries";
import { handle, json, readJson, refreshSite, requireUuid } from "../../../../../lib/api";
import { HttpError, requireOwner } from "../../../../../lib/portal-auth";
import { contactPatch } from "../../../../../lib/validation";

type Ctx = { params: Promise<{ id: string }> };

export const PATCH = handle(async (request: Request, ctx: Ctx) => {
  await requireOwner(request);
  const contact = await updateContact(requireUuid((await ctx.params).id), contactPatch.parse(await readJson(request)));
  if (!contact) throw new HttpError(404, "not_found", "Contact not found.");
  refreshSite();
  return json({ contact });
});

export const DELETE = handle(async (request: Request, ctx: Ctx) => {
  await requireOwner(request);
  if (!(await deleteContact(requireUuid((await ctx.params).id)))) throw new HttpError(404, "not_found", "Contact not found.");
  refreshSite();
  return json({ deleted: true });
});

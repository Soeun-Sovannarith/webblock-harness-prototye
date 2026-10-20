import { getBusiness, upsertBusiness } from "../../../db/queries";
import { handle, json, readJson, refreshSite } from "../../../lib/api";
import { requireOwner } from "../../../lib/portal-auth";
import { businessInput } from "../../../lib/validation";

export const dynamic = "force-dynamic";

/** Public: business information and contact methods shown on the website. */
export const GET = handle(async () => json(await getBusiness()));

export const PUT = handle(async (request: Request) => {
  await requireOwner(request);
  const profile = await upsertBusiness(businessInput.parse(await readJson(request)));
  refreshSite();
  return json({ profile });
});

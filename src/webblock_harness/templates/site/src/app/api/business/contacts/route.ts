import { addContact } from "../../../../db/queries";
import { handle, json, readJson, refreshSite } from "../../../../lib/api";
import { requireOwner } from "../../../../lib/portal-auth";
import { contactInput } from "../../../../lib/validation";

export const POST = handle(async (request: Request) => {
  await requireOwner(request);
  const contact = await addContact(contactInput.parse(await readJson(request)));
  refreshSite();
  return json({ contact }, 201);
});

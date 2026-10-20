import { revalidatePath } from "next/cache";
import { ZodError } from "zod";

import { HttpError } from "./portal-auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function json(data: unknown, status = 200): Response {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

export async function readJson(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    throw new HttpError(400, "invalid_json", "Request body must be JSON.");
  }
}

export function requireUuid(id: string): string {
  if (!UUID.test(id)) throw new HttpError(404, "not_found", "Not found.");
  return id;
}

/** Website pages read business data from the database; refresh them after the owner changes it. */
export function refreshSite() {
  revalidatePath("/", "layout");
}

export function handle<A extends unknown[]>(fn: (...args: A) => Promise<Response>) {
  return async (...args: A): Promise<Response> => {
    try {
      return await fn(...args);
    } catch (err) {
      if (err instanceof HttpError) return json({ error: { code: err.code, message: err.message, details: err.details } }, err.status);
      if (err instanceof ZodError)
        return json({ error: { code: "invalid_input", message: "Some fields are invalid.", details: err.issues.map((i) => ({ path: i.path.join("."), message: i.message })) } }, 422);
      if (err instanceof Error && err.message === "DATABASE_URL is not configured")
        return json({ error: { code: "database_not_configured", message: "This site is not connected to a database yet." } }, 503);
      console.error("[webblock-api]", err);
      return json({ error: { code: "internal_error", message: "Something went wrong." } }, 500);
    }
  };
}

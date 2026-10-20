import { importSPKI, jwtVerify, type JWTPayload } from "jose";

import { siteIdentity } from "./site-config";

export class HttpError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

let keyPromise: ReturnType<typeof importSPKI> | null = null;

/**
 * Business Portal access. WebBlock signs a short-lived token (Ed25519) for the signed-in owner of this site;
 * the site only needs WebBlock's *public* key (committed in webblock/site.json), so no secret lives in the repo.
 */
export async function requireOwner(request: Request): Promise<JWTPayload> {
  const header = request.headers.get("authorization") ?? "";
  if (!header.toLowerCase().startsWith("bearer ")) throw new HttpError(401, "unauthenticated", "Sign in through WebBlock to manage this site.");
  keyPromise ??= importSPKI(siteIdentity.portal.publicKey, "EdDSA");
  try {
    const { payload } = await jwtVerify(header.slice(7).trim(), await keyPromise, {
      issuer: siteIdentity.portal.issuer,
      audience: siteIdentity.portal.audience,
      algorithms: ["EdDSA"],
    });
    if (payload.scope !== "business_portal") throw new HttpError(403, "forbidden", "This token cannot manage the site.");
    return payload;
  } catch (err) {
    if (err instanceof HttpError) throw err;
    throw new HttpError(401, "invalid_token", "Your session has expired. Open the Business Portal again from WebBlock.");
  }
}

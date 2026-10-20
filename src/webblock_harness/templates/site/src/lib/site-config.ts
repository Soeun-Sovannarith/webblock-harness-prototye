import siteJson from "../../webblock/site.json";

/** Non-secret site identity written by WebBlock when the repository is created. */
export type SiteIdentity = {
  tenantId: string;
  siteName: string;
  portal: { issuer: string; audience: string; publicKey: string };
};

export const siteIdentity = siteJson as SiteIdentity;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function tenantId(): string {
  if (!UUID.test(siteIdentity.tenantId)) throw new Error("webblock/site.json has no valid tenantId");
  return siteIdentity.tenantId;
}

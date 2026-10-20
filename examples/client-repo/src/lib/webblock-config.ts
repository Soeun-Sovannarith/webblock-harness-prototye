// Runtime configuration delivery: content-only changes go live without a rebuild.
// Only the *published* revision is ever served by the harness.
import type { Data } from "@measured/puck";

type SiteConfig = { pages: Record<string, Data> };

export async function getPage(path: string): Promise<Data | null> {
  const res = await fetch(
    `${process.env.WEBBLOCK_HARNESS_URL}/v1/public/sites/${process.env.WEBBLOCK_SITE_KEY}/config`,
    { next: { tags: ["webblock-config"], revalidate: 300 } },
  );
  if (!res.ok) return null;
  const body = (await res.json()) as { config_hash: string; config: SiteConfig };
  return body.config.pages[path] ?? null;
}

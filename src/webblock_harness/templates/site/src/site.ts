import type { Data } from "@puckeditor/core";

import siteConfig from "../webblock/site-config.json";

/** Managed by WebBlock: edit content in the WebBlock editor (or carefully, in webblock/site-config.json). */
export type SiteConfig = { pages: Record<string, Data> };

export const site = siteConfig as unknown as SiteConfig;

export function pagePath(slug: string[] | undefined): string {
  return slug && slug.length ? `/${slug.join("/")}` : "/";
}

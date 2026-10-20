import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Render } from "@puckeditor/core/rsc";

import { withLiveData } from "../../lib/site-data";
import { puckConfig } from "../../puck.config";
import { pagePath, site } from "../../site";

type Params = { slug?: string[] };

export const dynamicParams = false;
// Products and contact details come from the database; pages refresh every minute, and immediately
// after the owner saves a change in the Business Portal.
export const revalidate = 60;

export function generateStaticParams(): Params[] {
  return Object.keys(site.pages).map((path) => ({ slug: path === "/" ? [] : path.slice(1).split("/") }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const root = site.pages[pagePath(slug)]?.root?.props as { title?: string; description?: string } | undefined;
  return { title: root?.title, description: root?.description };
}

export default async function Page({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const data = site.pages[pagePath(slug)];
  if (!data) notFound();
  return <Render config={puckConfig} data={await withLiveData(data)} />;
}

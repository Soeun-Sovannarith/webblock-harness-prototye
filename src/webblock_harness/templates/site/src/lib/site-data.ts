import type { Data } from "@puckeditor/core";

import { getDb } from "../db/client";
import { getBusiness, listProducts } from "../db/queries";

type Node = { type?: string; props?: Record<string, unknown> };

function walk(value: unknown, visit: (node: Node) => void) {
  if (Array.isArray(value)) value.forEach((v) => walk(v, visit));
  else if (value && typeof value === "object") {
    const node = value as Node;
    if (typeof node.type === "string" && node.props) visit(node);
    Object.values(node.props ?? (value as Record<string, unknown>)).forEach((v) => walk(v, visit));
  }
}

const fromDatabase = (node: Node) => (node.props?.source ?? "database") === "database";

/**
 * Fill blocks whose content comes from the Business Portal (products, contact details) with live data.
 * Without DATABASE_URL — or if the database is unreachable — the page keeps the content saved in the editor.
 */
export async function withLiveData(data: Data): Promise<Data> {
  let needsProducts = false;
  let needsBusiness = false;
  walk(data, (n) => {
    if (n.type === "ProductGallery" && n.props?.source === "database") needsProducts = true;
    if ((n.type === "ContactInfo" && fromDatabase(n)) || n.type === "ContactForm") needsBusiness = true;
  });
  const db = getDb();
  if (!db || (!needsProducts && !needsBusiness)) return data;
  try {
    const [products, business] = await Promise.all([
      needsProducts ? listProducts(false, db) : Promise.resolve(null),
      needsBusiness ? getBusiness(db) : Promise.resolve(null),
    ]);
    const live = structuredClone(data);
    walk(live, (n) => {
      const props = n.props!;
      if (n.type === "ProductGallery" && props.source === "database" && products) {
        props.products = products.map((p) => ({
          name: p.title, price: p.price, description: p.description, image: p.imageUrl, badge: p.badge,
        }));
        if (business?.profile?.currency) props.currency = business.profile.currency;
      }
      const profile = business?.profile;
      if (n.type === "ContactInfo" && fromDatabase(n) && business) {
        Object.assign(props, {
          email: profile?.email ?? props.email, phone: profile?.phone ?? props.phone, address: profile?.address ?? props.address,
          hours: profile?.openingHours ?? props.hours,
          contacts: business.contacts.map((c) => ({ label: c.label, kind: c.kind, value: c.value })),
        });
      }
      if (n.type === "ContactForm" && !props.email && profile?.email) props.email = profile.email;
    });
    return live;
  } catch (err) {
    console.error("[webblock] could not load live business data; showing saved content", err);
    return data;
  }
}

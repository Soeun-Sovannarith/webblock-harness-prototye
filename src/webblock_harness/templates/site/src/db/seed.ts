/**
 * Load the business details and products entered when the site was created (webblock/seed.json).
 * Only fills empty data: running it again never overwrites changes made in the Business Portal.
 */
import { eq, sql } from "drizzle-orm";

import seedJson from "../../webblock/seed.json";
import customFields from "../../webblock/custom-fields.json";
import { requireDb, withTenant } from "./client";
import { businessContacts, businessProfiles, products, siteSettings, type ProductCustomFields } from "./schema";

type Seed = {
  business: {
    businessName: string; businessType?: string | null; description?: string | null; logoUrl?: string | null;
    email?: string | null; phone?: string | null; address?: string | null; openingHours?: string | null; currency?: string;
  };
  products: { title: string; price: number; customFields?: ProductCustomFields }[];
  contacts: { label: string; kind: string; value: string }[];
};

async function main() {
  const seed = seedJson as Seed;
  const summary = await withTenant(async (tx, tenant) => {
    const done: string[] = [];
    const [profile] = await tx.select({ id: businessProfiles.tenantId }).from(businessProfiles).where(eq(businessProfiles.tenantId, tenant));
    if (!profile) {
      await tx.insert(businessProfiles).values({ tenantId: tenant, ...seed.business, currency: seed.business.currency ?? "USD" });
      done.push("business profile");
    }
    const [{ count: productCount }] = await tx.select({ count: sql<number>`count(*)::int` }).from(products).where(eq(products.tenantId, tenant));
    if (productCount === 0 && seed.products.length) {
      await tx.insert(products).values(seed.products.map((p, i) => ({
        tenantId: tenant, title: p.title, price: Number(p.price).toFixed(2), customFields: p.customFields ?? {}, sortOrder: i,
      })));
      done.push(`${seed.products.length} products`);
    }
    const [{ count: contactCount }] = await tx.select({ count: sql<number>`count(*)::int` }).from(businessContacts).where(eq(businessContacts.tenantId, tenant));
    if (contactCount === 0 && seed.contacts.length) {
      await tx.insert(businessContacts).values(seed.contacts.map((c, i) => ({ tenantId: tenant, ...c, sortOrder: i })));
      done.push(`${seed.contacts.length} contacts`);
    }
    await tx.insert(siteSettings).values({ tenantId: tenant, allowedCustomFields: customFields })
      .onConflictDoUpdate({ target: siteSettings.tenantId, set: { allowedCustomFields: customFields, updatedAt: new Date() } });
    return done;
  }, requireDb());
  console.log(summary.length ? `Seeded: ${summary.join(", ")}.` : "Nothing to seed: business data already exists.");
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

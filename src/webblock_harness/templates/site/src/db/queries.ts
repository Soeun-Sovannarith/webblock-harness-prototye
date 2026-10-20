import { and, asc, eq, sql } from "drizzle-orm";
import type { z } from "zod";

import type { businessInput, contactInput, contactPatch, productInput, productPatch } from "../lib/validation";
import { withTenant, type Db } from "./client";
import { businessContacts, businessProfiles, products, type ProductCustomFields } from "./schema";

export type PublicProduct = {
  id: string;
  title: string;
  price: number;
  status: string;
  description?: string;
  imageUrl?: string;
  badge?: string;
  customFields: Record<string, unknown>;
  sortOrder: number;
};

function toPublic(row: typeof products.$inferSelect): PublicProduct {
  const { description, imageUrl, badge, ...custom } = row.customFields ?? {};
  return {
    id: row.id, title: row.title, price: Number(row.price), status: row.status, sortOrder: row.sortOrder,
    description: description || undefined, imageUrl: imageUrl || undefined, badge: badge || undefined, customFields: custom,
  };
}

function fieldsFrom(input: Partial<z.infer<typeof productInput>>, existing: ProductCustomFields = {}): ProductCustomFields {
  const next: ProductCustomFields = { ...existing, ...((input.customFields ?? {}) as ProductCustomFields) };
  if (input.description !== undefined) next.description = input.description;
  if (input.imageUrl !== undefined) next.imageUrl = input.imageUrl;
  if (input.badge !== undefined) next.badge = input.badge;
  return next;
}

export async function listProducts(includeHidden: boolean, db?: Db): Promise<PublicProduct[]> {
  return withTenant(async (tx, tenant) => {
    const where = includeHidden ? eq(products.tenantId, tenant) : and(eq(products.tenantId, tenant), eq(products.status, "ACTIVE"));
    const rows = await tx.select().from(products).where(where).orderBy(asc(products.sortOrder), asc(products.createdAt));
    return rows.map(toPublic);
  }, db);
}

export async function createProduct(input: z.infer<typeof productInput>): Promise<PublicProduct> {
  return withTenant(async (tx, tenant) => {
    // New products go to the end of the list unless a position is given.
    const [{ next }] = await tx.select({ next: sql<number>`coalesce(max(${products.sortOrder}), -1)::int + 1` })
      .from(products).where(eq(products.tenantId, tenant));
    const [row] = await tx.insert(products).values({
      tenantId: tenant, title: input.title, price: input.price.toFixed(2), status: input.status,
      sortOrder: input.sortOrder ?? next, customFields: fieldsFrom(input),
    }).returning();
    return toPublic(row);
  });
}

export async function updateProduct(id: string, input: z.infer<typeof productPatch>): Promise<PublicProduct | null> {
  return withTenant(async (tx, tenant) => {
    const [current] = await tx.select().from(products).where(and(eq(products.id, id), eq(products.tenantId, tenant)));
    if (!current) return null;
    const [row] = await tx.update(products).set({
      ...(input.title !== undefined ? { title: input.title } : {}),
      ...(input.price !== undefined ? { price: input.price.toFixed(2) } : {}),
      ...(input.status !== undefined ? { status: input.status } : {}),
      ...(input.sortOrder !== undefined ? { sortOrder: input.sortOrder } : {}),
      customFields: fieldsFrom(input, current.customFields),
      updatedAt: new Date(),
    }).where(and(eq(products.id, id), eq(products.tenantId, tenant))).returning();
    return toPublic(row);
  });
}

export async function deleteProduct(id: string): Promise<boolean> {
  return withTenant(async (tx, tenant) => {
    const rows = await tx.delete(products).where(and(eq(products.id, id), eq(products.tenantId, tenant))).returning({ id: products.id });
    return rows.length > 0;
  });
}

export async function getBusiness(db?: Db) {
  return withTenant(async (tx, tenant) => {
    const [profile] = await tx.select().from(businessProfiles).where(eq(businessProfiles.tenantId, tenant));
    const contacts = await tx.select().from(businessContacts).where(eq(businessContacts.tenantId, tenant))
      .orderBy(asc(businessContacts.sortOrder), asc(businessContacts.createdAt));
    return {
      profile: profile ? { ...profile, tenantId: undefined } : null,
      contacts: contacts.map(({ tenantId: _t, ...c }) => c),
    };
  }, db);
}

export async function upsertBusiness(input: z.infer<typeof businessInput>) {
  return withTenant(async (tx, tenant) => {
    const values = {
      businessName: input.businessName, businessType: input.businessType ?? null, description: input.description ?? null,
      logoUrl: input.logoUrl || null, email: input.email || null, phone: input.phone ?? null, address: input.address ?? null,
      openingHours: input.openingHours ?? null, ...(input.currency ? { currency: input.currency } : {}), updatedAt: new Date(),
    };
    const [row] = await tx.insert(businessProfiles).values({ tenantId: tenant, ...values })
      .onConflictDoUpdate({ target: businessProfiles.tenantId, set: values }).returning();
    return { ...row, tenantId: undefined };
  });
}

export async function addContact(input: z.infer<typeof contactInput>) {
  return withTenant(async (tx, tenant) => {
    const [row] = await tx.insert(businessContacts).values({ tenantId: tenant, ...input, sortOrder: input.sortOrder ?? 0 }).returning();
    const { tenantId: _t, ...contact } = row;
    return contact;
  });
}

export async function updateContact(id: string, input: z.infer<typeof contactPatch>) {
  return withTenant(async (tx, tenant) => {
    const [row] = await tx.update(businessContacts).set(input)
      .where(and(eq(businessContacts.id, id), eq(businessContacts.tenantId, tenant))).returning();
    if (!row) return null;
    const { tenantId: _t, ...contact } = row;
    return contact;
  });
}

export async function deleteContact(id: string): Promise<boolean> {
  return withTenant(async (tx, tenant) => {
    const rows = await tx.delete(businessContacts)
      .where(and(eq(businessContacts.id, id), eq(businessContacts.tenantId, tenant))).returning({ id: businessContacts.id });
    return rows.length > 0;
  });
}

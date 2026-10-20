/**
 * Business data for this website, stored in WebBlock's shared Postgres database.
 *
 * Tables live in the dedicated `webblock` schema and every row carries `tenant_id`; Row-Level Security
 * policies (see drizzle/0000_webblock_init.sql) make each site see only its own rows.
 */
import { index, integer, jsonb, numeric, pgSchema, text, timestamp, uuid, varchar } from "drizzle-orm/pg-core";

export const webblock = pgSchema("webblock");

/** Extra product attributes. Standard keys plus the business-specific custom fields in webblock/custom-fields.json. */
export type ProductCustomFields = {
  description?: string;
  imageUrl?: string;
  badge?: string;
  [key: string]: string | number | boolean | null | undefined;
};

export const products = webblock.table(
  "products",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    tenantId: uuid("tenant_id").notNull(),
    title: varchar("title", { length: 255 }).notNull(),
    price: numeric("price", { precision: 12, scale: 2 }).notNull().default("0.00"),
    status: varchar("status", { length: 20 }).notNull().default("ACTIVE"), // ACTIVE | HIDDEN
    customFields: jsonb("custom_fields").$type<ProductCustomFields>().notNull().default({}),
    sortOrder: integer("sort_order").notNull().default(0),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [index("products_tenant_idx").on(t.tenantId)],
);

export const siteSettings = webblock.table("site_settings", {
  tenantId: uuid("tenant_id").primaryKey(),
  allowedCustomFields: jsonb("allowed_custom_fields").$type<unknown[]>().notNull().default([]),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export const businessProfiles = webblock.table("business_profiles", {
  tenantId: uuid("tenant_id").primaryKey(),
  businessName: varchar("business_name", { length: 80 }).notNull(),
  businessType: varchar("business_type", { length: 60 }),
  description: text("description"),
  logoUrl: text("logo_url"),
  email: varchar("email", { length: 200 }),
  phone: varchar("phone", { length: 40 }),
  address: varchar("address", { length: 300 }),
  openingHours: text("opening_hours"),
  currency: varchar("currency", { length: 3 }).notNull().default("USD"),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export const businessContacts = webblock.table(
  "business_contacts",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    tenantId: uuid("tenant_id").notNull(),
    label: varchar("label", { length: 60 }).notNull(),
    kind: varchar("kind", { length: 20 }).notNull(), // phone | email | whatsapp | telegram | facebook | instagram | website | other
    value: varchar("value", { length: 300 }).notNull(),
    sortOrder: integer("sort_order").notNull().default(0),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [index("business_contacts_tenant_idx").on(t.tenantId)],
);

export type Product = typeof products.$inferSelect;
export type BusinessProfile = typeof businessProfiles.$inferSelect;
export type BusinessContact = typeof businessContacts.$inferSelect;

import { z } from "zod";

import customFieldsJson from "../../webblock/custom-fields.json";

/** Business-specific product fields (generated from the template / business type). */
export type CustomFieldDef = {
  key: string;
  label: string;
  type: "text" | "number" | "boolean" | "select";
  options?: string[];
  required?: boolean;
};

export const customFieldDefs = customFieldsJson as CustomFieldDef[];

const RESERVED = new Set(["description", "imageUrl", "badge"]);
const imageUrl = z.string().trim().max(500).regex(/^(https:\/\/|\/(?!\/))[^\s<>"']*$/, "Use an https:// image URL");

function customFieldSchema(def: CustomFieldDef): z.ZodType {
  const base =
    def.type === "number" ? z.number().finite()
    : def.type === "boolean" ? z.boolean()
    : def.type === "select" ? z.enum((def.options ?? []) as [string, ...string[]])
    : z.string().trim().max(500);
  return def.required ? base : base.nullable().optional();
}

export const customFieldsSchema = z
  .object(Object.fromEntries(customFieldDefs.filter((d) => !RESERVED.has(d.key)).map((d) => [d.key, customFieldSchema(d)])))
  .strict();

export const productInput = z
  .object({
    title: z.string().trim().min(1).max(255),
    price: z.number().min(0).max(10_000_000),
    status: z.enum(["ACTIVE", "HIDDEN"]).default("ACTIVE"),
    description: z.string().trim().max(1000).optional(),
    imageUrl: imageUrl.optional().or(z.literal("")),
    badge: z.string().trim().max(30).optional(),
    sortOrder: z.number().int().min(0).max(100_000).optional(),
    customFields: customFieldsSchema.optional(),
  })
  .strict();

export const productPatch = productInput.partial().strict();

export const businessInput = z
  .object({
    businessName: z.string().trim().min(1).max(80),
    businessType: z.string().trim().max(60).optional().nullable(),
    description: z.string().trim().max(1000).optional().nullable(),
    logoUrl: imageUrl.optional().nullable().or(z.literal("")),
    email: z.email().max(200).optional().nullable().or(z.literal("")),
    phone: z.string().trim().max(40).optional().nullable(),
    address: z.string().trim().max(300).optional().nullable(),
    openingHours: z.string().trim().max(1000).optional().nullable(),
    currency: z.enum(["USD", "KHR"]).optional(),
  })
  .strict();

export const CONTACT_KINDS = ["phone", "email", "whatsapp", "telegram", "facebook", "instagram", "website", "other"] as const;

export const contactInput = z
  .object({
    label: z.string().trim().min(1).max(60),
    kind: z.enum(CONTACT_KINDS),
    value: z.string().trim().min(1).max(300),
    sortOrder: z.number().int().min(0).max(100_000).optional(),
  })
  .strict()
  .superRefine((c, ctx) => {
    if (c.kind === "email" && !z.email().safeParse(c.value).success) ctx.addIssue({ code: "custom", path: ["value"], message: "Not an email address" });
    if (["website", "facebook", "instagram"].includes(c.kind) && !/^https:\/\/[^\s<>"']+$/.test(c.value))
      ctx.addIssue({ code: "custom", path: ["value"], message: "Use a full https:// link" });
  });

export const contactPatch = z
  .object({ label: z.string().trim().min(1).max(60), value: z.string().trim().min(1).max(300), sortOrder: z.number().int().min(0).max(100_000) })
  .partial()
  .strict();

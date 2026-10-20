-- WebBlock business data (shared database, isolated `webblock` schema). Idempotent: safe to run for every site.
CREATE SCHEMA IF NOT EXISTS "webblock";
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "webblock"."products" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" uuid NOT NULL,
  "title" varchar(255) NOT NULL,
  "price" numeric(12, 2) DEFAULT '0.00' NOT NULL,
  "status" varchar(20) DEFAULT 'ACTIVE' NOT NULL,
  "custom_fields" jsonb DEFAULT '{}'::jsonb NOT NULL,
  "sort_order" integer DEFAULT 0 NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "products_tenant_idx" ON "webblock"."products" USING btree ("tenant_id");
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "webblock"."site_settings" (
  "tenant_id" uuid PRIMARY KEY NOT NULL,
  "allowed_custom_fields" jsonb DEFAULT '[]'::jsonb NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "webblock"."business_profiles" (
  "tenant_id" uuid PRIMARY KEY NOT NULL,
  "business_name" varchar(80) NOT NULL,
  "business_type" varchar(60),
  "description" text,
  "logo_url" text,
  "email" varchar(200),
  "phone" varchar(40),
  "address" varchar(300),
  "opening_hours" text,
  "currency" varchar(3) DEFAULT 'USD' NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "webblock"."business_contacts" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" uuid NOT NULL,
  "label" varchar(60) NOT NULL,
  "kind" varchar(20) NOT NULL,
  "value" varchar(300) NOT NULL,
  "sort_order" integer DEFAULT 0 NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "business_contacts_tenant_idx" ON "webblock"."business_contacts" USING btree ("tenant_id");
--> statement-breakpoint
-- Row-Level Security: a connection only sees rows of the tenant set with set_config('app.tenant_id', ...).
-- FORCE applies the policies to the table owner too. (Superusers always bypass RLS: connect with a normal role.)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['products', 'site_settings', 'business_profiles', 'business_contacts'] LOOP
    EXECUTE format('ALTER TABLE "webblock".%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE "webblock".%I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'webblock' AND tablename = t AND policyname = 'tenant_isolation') THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON "webblock".%I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
        t);
    END IF;
  END LOOP;
END $$;

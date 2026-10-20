import { sql } from "drizzle-orm";
import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import { tenantId } from "../lib/site-config";
import * as schema from "./schema";

export type Db = NodePgDatabase<typeof schema>;
type Tx = Parameters<Parameters<Db["transaction"]>[0]>[0];

const globalForDb = globalThis as unknown as { webblockPool?: Pool };

/** Null when DATABASE_URL is not configured (the site then renders the content stored in the editor). */
export function getDb(): Db | null {
  const url = process.env.DATABASE_URL;
  if (!url) return null;
  globalForDb.webblockPool ??= new Pool({ connectionString: url, max: 5 });
  return drizzle(globalForDb.webblockPool, { schema });
}

export function requireDb(): Db {
  const db = getDb();
  if (!db) throw new Error("DATABASE_URL is not configured");
  return db;
}

/**
 * Run queries as this site's tenant. Every query also filters by tenant_id explicitly; the session setting
 * additionally activates the Row-Level Security policies (defence in depth).
 */
export async function withTenant<T>(fn: (tx: Tx, tenant: string) => Promise<T>, db: Db = requireDb()): Promise<T> {
  const tenant = tenantId();
  return db.transaction(async (tx) => {
    await tx.execute(sql`select set_config('app.tenant_id', ${tenant}, true)`);
    return fn(tx, tenant);
  });
}

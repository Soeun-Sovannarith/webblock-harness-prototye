/** Create/upgrade the WebBlock tables in the shared database: `npm run db:migrate`. Idempotent. */
import path from "node:path";

import { migrate } from "drizzle-orm/node-postgres/migrator";

import { requireDb } from "./client";

async function main() {
  const db = requireDb();
  await migrate(db, { migrationsFolder: path.join(process.cwd(), "drizzle") });
  console.log("WebBlock database migrations applied.");
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

# __SITE_NAME__

Website generated and maintained by **WebBlock**: Next.js + Puck (pages) + Drizzle/Postgres (business data).

| What | Where | Edited with |
|---|---|---|
| Page layout, text, design | `webblock/site-config.json` (Puck data) | WebBlock editor → Deploy (a small commit) |
| Products, business info, contacts | WebBlock shared Postgres, schema `webblock` | Business Portal at `/business` — live, no redeploy |
| Components | `src/components/webblock/` | Code (must match the WebBlock registry) |

## Run locally

```bash
npm install
cp .env.example .env        # set DATABASE_URL (see below); without it the site shows the editor content
npm run db:setup            # create tables (idempotent) + load the business details entered in WebBlock
npm run dev                 # http://localhost:3000   ·   Business Portal: open it from the WebBlock dashboard
```

## Database

- Tables live in the `webblock` schema of the shared database; every row has `tenant_id`
  (this site's id is in `webblock/site.json`).
- Row-Level Security is enabled and **forced** on every table: a connection only sees rows for the tenant it sets
  (`set_config('app.tenant_id', …)`, done in `src/db/client.ts`). Connect with a **normal role** — superusers bypass RLS.
- `npm run db:migrate` is idempotent and safe to run on every deploy; `npm run db:seed` only fills empty data and never
  overwrites changes made in the Business Portal.

## API (used by the Business Portal)

| Method | Path | Access |
|---|---|---|
| GET | `/api/products` | public (active products) · `?include=all` owner |
| POST | `/api/products` | owner |
| PATCH / DELETE | `/api/products/:id` | owner |
| GET | `/api/business` | public (profile + contacts) |
| PUT | `/api/business` | owner |
| POST | `/api/business/contacts` | owner |
| PATCH / DELETE | `/api/business/contacts/:id` | owner |

"Owner" = a short-lived token signed by WebBlock for this site only (Ed25519). The site holds only WebBlock's
**public** key (`webblock/site.json`), so no secret is stored in this repository. Inputs are validated with zod
(including the business-specific product fields in `webblock/custom-fields.json`).

Edits made directly to `webblock/site-config.json` on GitHub are picked up by WebBlock and never overwritten by later
deploys unless the same value was changed in both places (WebBlock then stops and reports the conflict).

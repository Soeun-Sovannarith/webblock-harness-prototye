# WebBlock Harness

The **hybrid website-generation harness** for WebBlock, exposed as a FastAPI service.

It takes a client's change request (natural language, a Puck editor submission, or explicit operations),
decides *how* the change must be made, applies it, validates it, version-controls it, deploys it and
records an immutable snapshot only after the deployed version is verified.

> **Core rule.** Content changes never regenerate source code (Path A: targeted Puck JSON patches).
> Functional changes are never forced into JSON the registry can't represent (Path B: a minimal,
> validated source patch on an isolated branch). An LLM never makes the final decision.

## Contents

- [Website builder flow](#website-builder-flow)
- [Generated backend and Business Portal](#generated-backend-and-business-portal)
- [Starting point and assumptions](#starting-point-and-assumptions)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [The pipeline](#the-pipeline)
- [Change Analyzer](#change-analyzer)
- [Path A: runtime configuration](#path-a-runtime-configuration)
- [Path B: source-code changes](#path-b-source-code-changes)
- [Versions, snapshots and rollback](#versions-snapshots-and-rollback)
- [Security and tenant isolation](#security-and-tenant-isolation)
- [Client repository contract](#client-repository-contract)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Tests](#tests)
- [Recovery runbook](#recovery-runbook)
- [Limitations and next steps](#limitations-and-next-steps)

---

## Website builder flow

```
onboarding form ─► choose template ─► edit in Puck (draft) ─► Deploy ─► repo created in GitHub org + site pushed
                                                                  │
            edit again in Puck ◄──────────────────────────────────┘
                 │
                 └─► Deploy ─► only the changed values are committed to webblock/site-config.json
```

Run it locally:

```bash
uv run webblock-harness            # http://127.0.0.1:8080/app/  (needs DEV_LOGIN_ENABLED=true for local sign-in)
```

1. **Form → template** (`POST /v1/sites`): the business details become a Puck configuration built from one of
   three templates (`artisan-shop`, `cafe`, `modern-store`) using only registered components. A repository name is
   reserved in the organisation (`<business>-site`, never colliding with existing repos); nothing is pushed yet.
2. **Edit in Puck**: the editor (`editor/`, served at `/app/`) builds its fields from the harness registry and
   renders with **the same component files** that are pushed to the client repository, so the preview is the site.
   Saving stores a draft revision (`PUT /v1/projects/{id}/draft`).
3. **Deploy** (`POST /v1/projects/{id}/deploy`):
   - first time: a *provision* job validates the content, creates the repository in `GITHUB_ORG`, pushes the full
     standalone Next.js + Puck project ([`templates/site/`](src/webblock_harness/templates/site)) plus
     `webblock/site-config.json`, then verifies it on GitHub and records a snapshot;
   - afterwards: a *change* job diffs the draft against the version it was based on and replays **only those
     edits** onto the latest version; the config file is written as stable, pretty-printed JSON, so the commit diff is
     exactly the changed values (e.g. one `"price": 12` → `"price": 15` line).
4. **"Deployed"** (`DEPLOY_PROVIDER=github_push`, the default while there is no hosting/CI): the commit is on the
   default branch and the committed site configuration's hash matches the published revision — re-read from GitHub,
   not assumed. Swap in `github_actions` once a deploy workflow exists.
5. **Edits made directly on GitHub** are never overwritten: the editor calls `POST /v1/projects/{id}/sync` when it
   opens (imports valid edits and rebases unpublished editor changes on top), and every deploy reconciles with the
   file on GitHub. If the same value was changed in both places, the deploy stops with `repo_conflict`.

The generated site builds with `npm install && npm run build` (verified with Next.js 16.4.2 + `@puckeditor/core`
0.23.0: both pages prerender statically).

Editor development: `cd editor && npm install && npm run dev` (Vite on :5173, proxies `/v1` to :8080);
`npm run build` writes the bundle to `src/webblock_harness/static/app/`.

## Generated backend and Business Portal

Every site WebBlock creates ships with its own backend (spec Feature 06) and Business Portal (Feature 08):

```
WebBlock dashboard ──"Business Portal"──► POST /v1/projects/{id}/portal-session ──► Ed25519 token for this site only
                                                                                          │
   https://<site>/business#token=… ◄──────────────────────────────────────────────────────┘
        │  (token moved out of the URL immediately)
        ▼
   Next.js API routes (zod-validated) ──Drizzle──► shared Postgres, schema "webblock", tenant_id + forced RLS
        │                                                         ▲
        └── revalidatePath ─► website pages ──read live products/contacts──┘
```

- **Drizzle schema** (`webblock.products`, `webblock.business_profiles`, `webblock.business_contacts`,
  `webblock.site_settings`): a dedicated schema in the shared database, so the existing Prisma tables of your older
  stores are not touched. Products keep the old shape (`title`, `price`, `status`, `custom_fields` jsonb).
- **Custom fields** per template / business type (e.g. café: *Menu category*, *Available today*; artisan:
  *Materials*, *Made to order*, *In stock*) are written to `webblock/custom-fields.json`; they drive validation and the
  portal form. A shared database can't have per-tenant columns, so they're stored in `custom_fields`.
- **Row-Level Security**: enabled and forced on every table. Each request sets `app.tenant_id` in its transaction,
  and every query also filters by `tenant_id` explicitly.
- **Live data**: *Product gallery* and *Contact & location* blocks set to `source: database` render rows from the
  database; the editor's values are the preview and the fallback when no database is configured. Portal edits appear on
  the site immediately (`revalidatePath`), without a redeploy or a commit.
- **Seed**: the onboarding form (business info, opening hours, products) becomes `webblock/seed.json`;
  `npm run db:setup` creates the tables and loads it once.
- **Portal sign-in**: WebBlock holds an Ed25519 private key (`PORTAL_SIGNING_KEY`, or generated into
  `PORTAL_KEY_FILE` for development); each repo contains only the public key. Tokens are bound to one site
  (`aud = site:<tenant>`), carry `scope = business_portal`, and expire after 8 hours.

**Verified** against a real Postgres 16 (throwaway cluster) with a non-superuser role: migrations and seed are
idempotent; RLS returns 0 rows with no tenant or another tenant, and blocks cross-tenant inserts; the API rejects
missing tokens, tokens for other sites and invalid input; adding a product, updating the business info and
adding a contact showed up on the live home page straight away; the portal UI works in the browser.

**Running a generated site's backend** (until CI/CD exists): create a normal Postgres role, set `DATABASE_URL` in
the site's `.env`, then `npm run db:setup && npm run build && npm start`. Set the site URL on the dashboard card
(default `http://localhost:3000`) so the Business Portal button opens the right place.

**Sites deployed with older WebBlock code** (e.g. before the backend existed) show *Update site code* on their
dashboard card (`code_update_available` in `ProjectView`; `POST /v1/projects/{id}/update-code`). The update job
pushes one commit with the missing/outdated code files. A file is only replaced if it still matches what WebBlock last
wrote (tracked per file in `site_files_manifest`, or read from the first-deploy commit for older sites); files edited
on GitHub are kept and listed in the job events, and the page content (`webblock/site-config.json`) is never touched.

## Starting point and assumptions

The working directory contained only `Harness_Prompt.txt` and a `.env` with a GitHub organisation token —
no existing WebBlock application, Puck integration, ORM, CI or deployment code to inspect or preserve.
The harness is therefore a **standalone Python service** (the "AI service" in WebBlock's architecture),
built so it can slot in next to the rest of the platform without assuming their internals:

| Concern | Decision | Why / how to change |
|---|---|---|
| Identity | Verifies a signed JWT from the WebBlock auth service (Spring Boot). Tenant and roles come **only** from token claims. | Claim names, issuer, audience and HS/RS algorithms are configurable. |
| Persistence | SQLAlchemy 2 — SQLite by default, PostgreSQL via `DATABASE_URL`. | No existing ORM to preserve. Every tenant-owned table has `tenant_id`; Postgres RLS can be layered on (see [next steps](#limitations-and-next-steps)). |
| Large artifacts | Content-addressed files under `ARTIFACT_DIR`, tenant-prefixed keys. | `ArtifactStore` protocol; swap in S3/GCS. |
| Client repos | GitHub REST (Git Data API, PRs, check runs). No local clones, no shell. | `GitProvider` protocol (`InMemoryGitProvider` for tests). |
| Build & deploy | GitHub Actions `workflow_dispatch` on the client repo. | `DeployProvider` protocol (`FakeDeployProvider` for tests). |
| LLM | A local model through Ollama (`LLM_PROVIDER=ollama`, e.g. `qwen2.5-coder:7b`, output constrained to a JSON schema), or Claude (`claude-opus-5-5`) via the Anthropic SDK with structured outputs, adaptive thinking and server-side refusal fallback. | `Planner` protocol; `LLM_PROVIDER=none` gives rules-only operation. |
| Component registry | Trusted JSON registry shipped with the harness (pottery-friendly defaults) + per-project extensions created by Path B. | `COMPONENT_REGISTRY_PATH` points at your real registry. |

## Quick start

```bash
uv sync                                   # Python 3.14, installs deps + dev tools
# Add to .env (see .env.example): AUTH_JWT_SECRET (or AUTH_JWT_PUBLIC_KEY), GITHUB_ORG,
# GITHUB_ORGANIZATION_KEY (already there), and ANTHROPIC_API_KEY for Path B.

uv run webblock-harness                   # serves on :8080 (OpenAPI docs at /docs)
uv run python scripts/dev_token.py --tenant pottery-co --roles admin   # local dev JWT
uv run python scripts/demo.py             # offline end-to-end demo (no GitHub, no LLM, no network)
uv run pytest                             # 122 tests
uv run ruff check src tests && uv run mypy
```

Register a project (admin), then submit changes:

```bash
TOKEN=$(uv run python scripts/dev_token.py --tenant pottery-co --roles admin)
curl -X POST localhost:8080/v1/projects -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"name": "Kampot Clay", "repo_name": "kampot-clay", "initial_config": {...puck data...}}'
curl -X POST localhost:8080/v1/projects/$PROJECT/jobs -H "Authorization: Bearer $TOKEN" \
  -H 'Idempotency-Key: req-001' -H 'content-type: application/json' \
  -d '{"request": "Change the Lotus Tea Cup price from $12 to $15"}'
curl localhost:8080/v1/jobs/$JOB/events -H "Authorization: Bearer $TOKEN"
```

## Architecture

```
            Spring Boot auth / gateway ──(signed JWT: sub, tenant_id, roles)──┐
                                                                             ▼
 Puck editor / chat UI ──► FastAPI (api/) ──► services/ ──────────────────────────────────────────┐
                              │               ├─ projects.py   tenant-scoped projects, revisions,   │
                              │               │                drafts, registry extensions          │
                              │               ├─ jobs.py       job lifecycle, events, locks,        │
                              │               │                approve / clarify / cancel / retry   │
                              │               ├─ pipeline.py   the stage machine (below)            │
                              │               └─ worker.py     background executor + recovery       │
                              │                                                                     │
                              ├─ analyzer/  intent rules · NL resolver · classifier · LLM planner   │
                              ├─ puck/      normalise · structural diff · targeted ops · validation │
                              ├─ codegen/   static validation of generated patches                  │
                              ├─ registry/  trusted component registry                              │
                              ├─ security/  JWT identity · path policy · untrusted-input handling   │
                              └─ integrations/ GitHub · GitHub Actions deploy · artifact store ◄────┘
                                                   │                    │
                         client repo (WebBlock org)◄┘                    └► deploy workflow ► live site
 Live client site ──GET /v1/public/sites/{site_key}/config──► published runtime configuration
```

| Module | Responsibility |
|---|---|
| `domain.py` | Typed contracts: `JobState` + allowed transitions, `ChangePlan`, `ChangeItem`, `PuckOp`s, `ValidationReport`, `CodeChangeProposal`, `HarnessError`. |
| `models.py` | Tables: tenants, projects, config_revisions, component_definitions, jobs, job_events, job_commits, deployments, snapshots. |
| `puck/` | Canonical JSON, semantic diff by component id (LCS-based reorder detection), atomic id-addressed operations with `expect` preconditions, registry schema validation. |
| `analyzer/` | Deterministic intent signals, rules for common edits, the classifier (final authority), Claude planner behind a protocol. Prompts (`prompts.py`) hold no business rules. |
| `codegen/validator.py` | Path policy, protected files, plan conformance, base-revision conflicts, static security scan, registry spec checks, diff review & approval re-derivation. |
| `services/pipeline.py` | Re-entrant stages; every external side effect is recorded before moving on. |

## The pipeline

A harness execution is a persistent **job** with a unique id, explicit states and an append-only event log
(`GET /v1/jobs/{id}/events`). Transitions are validated against `ALLOWED_TRANSITIONS`.

```
queued → analyzing → planned → [awaiting_approval] → applying → validating → committing → building → deploying → verifying → succeeded
             │                                          │            │            │            │           │           │
             ├→ needs_clarification (waits for client)  └────────────┴────────────┴────────────┴───────────┴───────────┴→ failed (retryable or not)
             └→ succeeded (no-op)                                                                       any pre-merge state → cancelled
```

| Stage | Input | Output |
|---|---|---|
| **queued** | Job record | Project lock acquired (one active job per project); otherwise stays queued behind the holder. |
| **analyzing** | Request / proposed config / ops; published config; registry (+ active extensions); repo head, file list and history | Hashed `ChangePlan`; base revision + base commit captured. Routes to no-op, clarification, rejection or `planned`. |
| **planned** | Plan | `awaiting_approval` if the plan has approval reasons and no approval bound to this plan hash; else `applying`. |
| **applying** | Plan + *current* published config / repo head | **A:** new *proposed* revision from targeted ops. **B:** generated patch → static validation → commit on `webblock/job-<id>` → PR. |
| **validating** | Proposed revision / branch commit | **A:** schema + registry-conformance → *validated* revision. **B:** waits for required CI check runs on the branch commit. |
| **committing** | Validated work | **B:** stale-base check → rebase (disjoint) or conflict (overlap) → squash-merge PR with head-SHA guard. **A (commit mode):** CAS commit of the config file. |
| **building** | Merged commit | Deployment record (attempt n) → `workflow_dispatch` (idempotent run lookup) → poll. |
| **deploying** | Build result | **A (runtime):** move the published-revision pointer (+ revalidate hook). **B:** wait for deploy; only then publish config that references new components. |
| **verifying** | Deployment | Served config hash matches; live site reports the deployed commit. On success: immutable snapshot, registry extensions activated, draft rebased. On failure: pointer restored, snapshot untouched. |

Long waits are polled one step at a time (no DB transaction is held open). Worker restarts resume
in-flight jobs (`Worker.recover`); stage handlers reuse recorded commits, PRs, proposals and deployments.

## Change Analyzer

Compares the request/proposal against the **last deployed configuration**, the **registry**, the
**repository's files and history**, and the **requested outcome**:

1. **Security scan** of untrusted text (prompt injection, secret access, cross-tenant references, command execution). Blocking flags reject the request outright; non-blocking flags are recorded.
2. **Candidate operations** from, in order: explicit ops → editor submission (structural diff converted to targeted ops) → deterministic NL rules (prices, quoted text, move/swap, add/remove registered components) → Claude (only when rules don't match and intent isn't a capability request).
3. **Apply to the base and diff structurally.** Classification always runs on the real structural diff of the candidate, never on what a model claims it did. Key order, whitespace and `15` vs `15.0` are ignored.
4. **Registry ↔ source check**: a registered component whose source file is missing from the repo is not runtime-safe.
5. **Classifier** (deterministic): unsupported components / new behaviour / API / DB changes → Path B; invalid properties → rejected; empty diff → no-op; otherwise Path A. LLM disagreements are recorded in `llm_overridden`.
6. **Path B planning**: Claude produces a `SourcePlan` (files, endpoints, migrations, dependencies, tests); rules derive approvals from it (DB schema, destructive migrations, auth, payments, tenant isolation, dependencies, edits to user-authored files, deletions).
7. Ambiguity (multiple targets, unknown target, destructive scope, low LLM confidence, plan questions) → **clarification**, never a guess.

The plan states what changed, affected pages/components, the path and why, expected files, approvals,
required validations, assumptions, and `decided_by` (`rules` or `rules+llm`). Try it without side effects:
`POST /v1/projects/{id}/analyze`.

## Path A: runtime configuration

- Ops address components by Puck `props.id` and carry `expect` preconditions, so a patch made against a stale page fails as a conflict instead of overwriting newer content. Unrelated content is untouched.
- Every revision is immutable and content-addressed; drafts, proposed, validated and published revisions are separate. A failed job never moves the published pointer.
- **`CONFIG_DELIVERY_MODE=runtime`** (default): the site fetches `GET /v1/public/sites/{site_key}/config` (only the published revision, with ETag); publishing is a pointer move + optional revalidation webhook — no build.
- **`CONFIG_DELIVERY_MODE=commit`**: for sites that bundle config at build time. The config file (`webblock/site-config.json`) is committed (CAS) and shipped through the normal build — still no code generation. This is the least expensive safe alternative when runtime delivery isn't available.

## Path B: source-code changes

1. Structured plan (analysis) → approval if required (bound to the plan hash).
2. Reads only the files the plan needs (never protected files such as `.env*`) and asks Claude for complete file contents plus registry specs for new components.
3. **Static validation before anything is committed**: canonical paths, scope write-zones (frontend patches can't touch `src/app/api/**`), protected files, plan conformance (no unplanned files/deps/migrations), base-SHA conflicts, forbidden constructs (`eval`, `new Function`, `child_process`, `dangerouslySetInnerHTML`, internal hosts, computed `process.env`, env vars outside `GENERATED_CODE_ENV_ALLOWLIST`, server env in client code, interpolated SQL, unvalidated mutating API routes, hardcoded secrets), destructive SQL, component spec + export, diff size / large rewrites, and approvals re-derived from what the patch *actually* does.
4. Commit to an isolated job branch + PR. **Format, typecheck, lint, tests, build, integration and migration tests run in the client repo's CI** — an isolated runner without WebBlock credentials. Generated code is never executed on the harness host.
5. Merge only when every required check passed, with the PR head SHA pinned. If `main` moved: overlapping files → `source_conflict` (manual edits are never overwritten); disjoint → rebase and re-run CI.
6. Deploy → verify → only then publish configuration that places the new component, and activate it in the registry.

## Versions, snapshots and rollback

| Pointer (on `projects`) | Meaning |
|---|---|
| `draft_revision_id` | The editor's working copy (`PUT /draft`, optimistic concurrency). |
| job `proposed_revision_id` | The latest proposed configuration for a job. |
| `last_validated_revision_id` | Last revision that passed validation. |
| `published_revision_id` | What the public endpoint serves. |
| `last_successful_snapshot_id` | Last **verified** deployment. |
| `source_commit_sha` | Current source revision on the default branch (as produced/observed by the harness). |

A **snapshot** is immutable (enforced by an ORM guard) and records tenant/project, config revision + hash,
commit SHA (and how it was determined), registry hash, plan hash, job, deployment, validation, build and
verification details; the full manifest is stored as an artifact. The last-successful snapshot advances
**only** in `verifying`, never on generation, validation, commit or build success.

**Rollback** (`POST /v1/projects/{id}/rollback`, owner): restores the snapshot's source tree as a *new*
commit on the default branch (history is never rewritten), redeploys it, republishes its configuration,
verifies, and records a new snapshot with `rollback_of_snapshot_id`.

## Security and tenant isolation

- Tenant and roles come only from the verified JWT (`exp`, `iss`, `aud`, signature). Request bodies, paths and custom headers like `X-Tenant-Id` are ignored. Forwarding by the gateway is not trusted on its own: every request must carry a verifiable token.
- Every query filters by the caller's tenant; other tenants' projects, jobs, revisions, snapshots, deployments, diffs and artifacts return **404** (existence is not revealed).
- Roles: `viewer` (read) < `editor` (drafts, jobs, clarify, retry) < `owner` (approve, rollback) < `admin` (register repositories).
- Repositories are always resolved inside `GITHUB_ORG`, verified to exist via the API, and may be mapped to exactly one project.
- GitHub and deploy credentials live only in server-side providers (`SecretStr`, redacted from logs). Nothing in the API returns them, and generated code may not read them.
- Client prompts, uploaded configs, repository contents and LLM output are untrusted: they are fenced as data in prompts, schema-validated, and every rule is enforced in code.

## Client repository contract

See [`examples/client-repo/`](examples/client-repo/):

- `.github/workflows/ci.yml` — job names are the check-run names the harness requires (`REQUIRED_CHECKS_*`).
- `.github/workflows/deploy.yml` — `workflow_dispatch` with `commit_sha` and `deployment_id` inputs, and `run-name` containing the deployment id (used for idempotent run discovery).
- `src/app/api/webblock/version/route.ts` — returns `{"commit_sha": ...}` for `DEPLOY_VERIFY_URL_TEMPLATE`.
- `src/lib/webblock-config.ts` + `src/app/api/webblock/revalidate/route.ts` — runtime config delivery.
- Registered components live under `src/components/webblock/` (paths in the registry); generated ones under `src/components/generated/`.

## API reference

All `/v1` routes except `/v1/public/*` require `Authorization: Bearer <jwt>`. Errors use
`{"error": {"code", "message", "details"}}`. Interactive docs: `/docs`.

| Method & path | Role | Purpose |
|---|---|---|
| `GET /health`, `GET /ready` | – | Liveness; readiness (DB + which providers are configured, no secrets). |
| `GET /v1/me` | any | Identity as the harness sees it. |
| `POST /v1/projects` | admin | Map a repo in the org to a new project (+ initial Puck config). |
| `GET /v1/projects`, `GET /v1/projects/{id}` | viewer | Projects and their version pointers. |
| `GET /v1/projects/{id}/context` | viewer | Pages/components, registry, source head (and whether it moved outside the harness), last snapshot. |
| `GET /v1/projects/{id}/registry` | viewer | Trusted registry incl. active project extensions. |
| `GET/PUT /v1/projects/{id}/draft` | viewer / editor | Editor draft (PUT requires `expected_draft_revision_id`). |
| `GET /v1/projects/{id}/revisions[/{rev}]` | viewer | Revision history; any revision's config (preview). |
| `POST /v1/projects/{id}/analyze` | viewer | Dry-run change plan. |
| `POST /v1/projects/{id}/jobs` | editor | Submit `request` \| `proposed_config` \| `operations` \| `use_draft`. Honors `Idempotency-Key`. |
| `GET /v1/projects/{id}/jobs`, `GET /v1/jobs/{id}` | viewer | Job state, plan, validation, commits, deployment, error. |
| `GET /v1/jobs/{id}/events` | viewer | Structured stage transitions and diagnostics. |
| `GET /v1/jobs/{id}/diff` | viewer | Unified diff of a Path B patch. |
| `POST /v1/jobs/{id}/approve` | owner | Approve, bound to `plan_hash`. |
| `POST /v1/jobs/{id}/clarify` | editor | Answer clarification questions. |
| `POST /v1/jobs/{id}/cancel` | editor | Cancel before merge/deploy. |
| `POST /v1/jobs/{id}/retry` | editor | Resume a retryable failure from its stage. |
| `GET /v1/projects/{id}/snapshots[/{snap}]`, `GET /v1/projects/{id}/deployments` | viewer | History, manifests, deployment attempts. |
| `POST /v1/projects/{id}/rollback` | owner | Roll back to a snapshot. |
| `GET /v1/public/sites/{site_key}/config` | public | Published runtime configuration (ETag / 304). |

## Configuration

Environment variables (or `.env`); lists are JSON (`["a","b"]`). See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_JWT_SECRET` / `AUTH_JWT_PUBLIC_KEY` | – (one required) | Verify gateway-issued tokens (HS* / RS*/ES*). |
| `AUTH_JWT_ALGORITHMS`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE` | `["HS256"]`, –, – | Token verification. |
| `AUTH_TENANT_CLAIM`, `AUTH_ROLES_CLAIM` | `tenant_id`, `roles` | Claim names. |
| `GITHUB_ORG` / `GITHUB_ORGANIZATION_NAME` | – | Organisation that owns client repos. |
| `GITHUB_ORGANIZATION_KEY` / `GITHUB_TOKEN` | – | GitHub token (server-side only). |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise support. |
| `HARNESS_BRANCH_PREFIX` | `webblock/job-` | Job branches (the only refs the harness may force-update). |
| `HARNESS_COMMIT_AUTHOR_NAME/EMAIL` | `WebBlock Harness` / `harness@webblock.invalid` | Commit identity; also how user-authored files are recognised. |
| `ANTHROPIC_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_EFFORT`, `LLM_MAX_TOKENS` | –, `anthropic`, `claude-opus-5-5`, `high`, `64000` | Planner. |
| `OLLAMA_BASE_URL`, `OLLAMA_NUM_CTX`, `OLLAMA_TIMEOUT_SECONDS` | `http://localhost:11434`, `32768`, `600` | Local model when `LLM_PROVIDER=ollama`. Small models also get a flat list of the page's editable values and a worked example. |
| `DATABASE_URL`, `ARTIFACT_DIR` | `sqlite:///./var/harness.db`, `./var/artifacts` | Persistence (install `psycopg` for Postgres). |
| `CONFIG_DELIVERY_MODE`, `CONFIG_REPO_PATH` | `commit`, `webblock/site-config.json` | How published config reaches sites (`commit`: in the repo; `runtime`: fetched from the harness). |
| `DEPLOY_PROVIDER`, `DEPLOY_WORKFLOW_FILE` | `github_push`, `deploy.yml` | `github_push` = verified push to the org repo; `github_actions` = dispatch a deploy workflow. |
| `NEW_REPO_PRIVATE`, `NEW_REPO_SUFFIX` | `true`, `-site` | Repositories created for new sites. |
| `DEV_LOGIN_ENABLED` | `false` | Local email sign-in for the bundled web app. **Development only.** |
| `PORTAL_SIGNING_KEY` / `PORTAL_KEY_FILE` | – / `./var/portal_signing_key.pem` | Ed25519 key that signs Business Portal tokens. Set the PEM in production; the file is auto-created for development. **Keep it stable**: deployed sites trust its public key. |
| `DEFAULT_SITE_URL` | `http://localhost:3000` | Where the Business Portal opens when a site has no URL set yet. |
| `DEPLOY_VERIFY_URL_TEMPLATE` | – | `{repo_name}`/`{owner}` URL reporting the live commit. **Without it, code deployments can't be verified and won't be snapshotted.** |
| `RUNTIME_REVALIDATE_URL_TEMPLATE`, `RUNTIME_REVALIDATE_SECRET` | – | Called after a runtime publish. |
| `PREVIEW_URL_TEMPLATE` | – | `{site_key}`, `{revision_id}` preview link recorded on jobs. |
| `REQUIRED_CHECKS_FRONTEND/BACKEND/DATABASE/CONFIG_COMMIT` | see `.env.example` | CI check names that must pass. |
| `MAX_PATCH_FILES`, `MAX_PATCH_CHANGED_LINES`, `MAX_FILE_BYTES`, `GENERATED_CODE_ENV_ALLOWLIST` | 25, 2500, 200000, `["DATABASE_URL","NEXT_PUBLIC_*"]` | Patch policy. |
| `JOB_EXECUTION`, `JOB_WORKERS`, `POLL_INTERVAL_SECONDS`, `CI_TIMEOUT_SECONDS`, `DEPLOY_TIMEOUT_SECONDS`, `PROJECT_LOCK_TTL_SECONDS` | `background`, 4, 10, 1800, 1800, 3600 | Execution. |
| `GIT_PROVIDER` | `github` | `memory` = in-memory repos (tests/dev only). |
| `COMPONENT_REGISTRY_PATH` | bundled | Your trusted registry JSON. |

## Tests

`uv run pytest` — 122 tests, all offline (in-memory GitHub, fake deploys, scripted planner, mocked HTTP for the
real GitHub/Actions/Anthropic adapters).

| Scenario | Tests (`tests/test_scenarios.py` unless noted) |
|---|---|
| A content-only | `test_scenario_a_*`: one leaf changes, no planner/git/deploy calls, revision + snapshot recorded. |
| B reorder | `test_scenario_b_*`: reorder detected, runtime path, rendered order. |
| C unsupported component | `test_scenario_c_*`: source path, branch → CI → merge → deploy, existing files intact, registry extended. |
| D backend feature | `test_scenario_d_*`: FE/BE/DB scopes, approval bound to plan hash, integration + migration checks required, destructive SQL blocked. |
| E invalid code | `test_scenario_e_*`: static validation and CI failures; nothing committed/merged; previous deployment live. |
| F concurrency | `test_scenario_f_*`: project lock, stale preconditions, editor save conflicts, overlapping manual edits rejected, disjoint edits rebased + re-validated. |
| G tenant isolation | `test_scenario_g_*`: 404s across every resource, repo mapping rules, auth, artifact scoping. |
| H deployment failure | `test_scenario_h_*`: failure recorded, snapshot unchanged, retry = new deploy attempt with no new commits, unverified deploys don't advance, idempotency keys. |
| I no-op | `test_scenario_i_*`: no revision, commit or deployment. |
| Website builder flow | `test_site_flow.py`: form → template → draft → first deploy creates the repo → redeploy commits only the changed line; GitHub edits kept, same-value conflicts stopped, sync + draft rebase, tenant isolation, repo-name collisions, every template deploys, scaffold ↔ registry consistency. |
| Generated backend | `test_business_backend.py`: first deploy pushes the Drizzle schema/migrations/API/portal, site identity + public key + custom fields + seed; no secrets in the repo; RLS on every table; portal tokens scoped to one site and owner; site URL validation; DB column backfill. |
| Units | `test_puck_engine.py`, `test_analyzer.py`, `test_lifecycle.py` (state machine, immutability, pointers, drafts, rollback, commit mode, background worker), `test_security_and_adapters.py`. |

## Recovery runbook

| Symptom (`error.code`) | What happened | Recovery |
|---|---|---|
| `config_conflict`, `plan_rejected` (stale `expect`) | Page changed since the request was analysed. | Submit the request again. |
| `source_conflict` | Someone edited the same files on `main`. | Their edits were kept. Submit again (or retry when the failure stage is `analyzing`) to re-plan on the new head. |
| `patch_validation_failed` | Generated patch broke a rule; nothing committed. | Read `error.details.issues`; rephrase or approve where `approval_required`. |
| `ci_failed` / `ci_timeout` | Required checks failed / never reported. | Check URLs are in `error.details`; ensure CI job names match `REQUIRED_CHECKS_*`. `ci_timeout` is retryable. |
| `build_failed`, `deployment_failed`, `verification_failed` | The live site still runs the last snapshot (config pointer restored). | `POST /v1/jobs/{id}/retry` — a new deployment attempt of the same merged commit; no new commits. |
| Bad version is live | — | `POST /v1/projects/{id}/rollback` with a previous snapshot id. |
| Process crashed mid-job | — | On restart, `Worker.recover()` resumes in-flight jobs; stale project locks expire after `PROJECT_LOCK_TTL_SECONDS`. |
| `llm_unavailable`, `llm_rate_limited`, `llm_truncated` | Planner problem. | Fix configuration / wait, then retry. |

## Limitations and next steps

- **Not connected to real WebBlock services yet.** There was no existing app to integrate with; the GitHub, Actions and Anthropic adapters are tested against mocked HTTP only — no real repository, workflow or model call was made. Point a staging org at the harness and run one job per path before production.
- **CI is the execution sandbox.** The harness does no local `tsc`/`eslint`/`next build`; it relies on the client repo's CI (`examples/client-repo/.github/workflows/ci.yml`). Use branch protection on `main` requiring the same checks.
- **Dependencies & lockfiles**: `package.json` changes need approval and lockfiles are protected, so plans that add npm packages can't complete automatically yet (lockfile regeneration would need a CI step that commits it).
- **Static scan is heuristic**: it catches obvious hazards; CI tests and review remain the real gate for logic and authorization bugs.
- **Postgres RLS** is not configured (no existing database to preserve); tenant scoping is enforced in the service layer. Adding RLS policies on `tenant_id` + a per-request `SET app.tenant_id` is the recommended hardening step.
- **Single process locks**: project locks are DB-based (safe across processes), but job dispatch uses an in-process thread pool; for multiple API replicas, move dispatch to a queue (e.g. a `SELECT … FOR UPDATE SKIP LOCKED` poller).
- **Deploy verification** requires `DEPLOY_VERIFY_URL_TEMPLATE`; without it, code deployments fail verification by design rather than being recorded as successful.
- No migrations tool (tables are created on start); add Alembic before the first schema change in production.

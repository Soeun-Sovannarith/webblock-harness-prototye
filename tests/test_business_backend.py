"""Generated backend (Drizzle + Next.js API routes) and Business Portal sign-in.

The generated TypeScript itself was verified separately against a real Postgres 16 (migrations, seed,
Row-Level Security, API routes, live pages); these tests cover what the harness generates and issues.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt as pyjwt
import pytest
from sqlalchemy import text

from conftest import ORG, Harness, build_harness
from webblock_harness.db import Database
from webblock_harness.models import Project
from webblock_harness.templates.catalog import TEMPLATES
from webblock_harness.templates.scaffold import SITE_DIR

PROFILE: dict[str, Any] = {
    "business_name": "Sok Cafe",
    "business_type": "Café",
    "tagline": "Coffee by the river",
    "email": "hello@sok.example",
    "phone": "+855 12 345 678",
    "address": "12 Riverside, Phnom Penh",
    "opening_hours": "Mon–Sun 7:00–18:00",
    "logo_url": "https://example.com/logo.png",
    "products": [
        {"name": "Iced Latte", "price": 3.5, "description": "Palm sugar latte", "image": "https://example.com/latte.jpg"}
    ],
}


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    with build_harness(
        tmp_path, register=False, CONFIG_DELIVERY_MODE="commit", DEPLOY_PROVIDER="github_push", DEV_LOGIN_ENABLED="true"
    ) as harness:
        harness.tokens["owner"] = harness.client.post("/v1/dev/session", json={"email": "owner@sok.example"}).json()["token"]
        harness.tokens["other"] = harness.client.post("/v1/dev/session", json={"email": "x@other.example"}).json()["token"]
        yield harness


def _hdr(h: Harness, who: str = "owner") -> dict[str, str]:
    return {"Authorization": f"Bearer {h.tokens[who]}"}


def _deployed_site(h: Harness, template: str = "cafe") -> tuple[dict[str, Any], dict[str, str]]:
    site = h.client.post("/v1/sites", json={"template_id": template, "profile": PROFILE}, headers=_hdr(h)).json()
    job = h.client.post(f"/v1/projects/{site['id']}/deploy", headers=_hdr(h)).json()
    assert job["state"] == "succeeded", job["error"]
    repo = h.git.repos[f"{ORG}/{site['repository'].split('/')[1]}"]
    return site, repo.commits[repo.branches["main"]].files


def test_first_deploy_pushes_drizzle_backend_and_business_portal(h: Harness) -> None:
    site, files = _deployed_site(h)
    for path in [
        "drizzle.config.ts",
        "drizzle/0000_webblock_init.sql",
        "drizzle/meta/_journal.json",
        "src/db/schema.ts",
        "src/db/client.ts",
        "src/db/queries.ts",
        "src/db/migrate.ts",
        "src/db/seed.ts",
        "src/app/api/products/route.ts",
        "src/app/api/products/[id]/route.ts",
        "src/app/api/business/route.ts",
        "src/app/api/business/contacts/route.ts",
        "src/app/api/business/contacts/[id]/route.ts",
        "src/app/business/page.tsx",
        "src/lib/portal-auth.ts",
        "src/lib/site-data.ts",
        ".env.example",
    ]:
        assert path in files, path
    pkg = json.loads(files["package.json"])
    assert {"drizzle-orm", "pg", "zod", "jose"} <= set(pkg["dependencies"])
    assert pkg["scripts"]["db:setup"] == "npm run db:migrate && npm run db:seed"

    identity = json.loads(files["webblock/site.json"])
    assert identity["tenantId"] == site["data_tenant_id"]
    assert identity["portal"]["audience"] == f"site:{site['data_tenant_id']}"
    assert identity["portal"]["publicKey"] == h.container.signer().public_key_pem

    # Custom product fields come from the template; the seed is the onboarding form.
    assert json.loads(files["webblock/custom-fields.json"]) == list(TEMPLATES["cafe"].custom_fields)
    seed = json.loads(files["webblock/seed.json"])
    assert seed["business"]["openingHours"] == "Mon–Sun 7:00–18:00"
    assert seed["business"]["logoUrl"] == "https://example.com/logo.png"
    assert seed["products"] == [
        {
            "title": "Iced Latte",
            "price": 3.5,
            "customFields": {"description": "Palm sugar latte", "imageUrl": "https://example.com/latte.jpg"},
        }
    ]

    # Blocks read live data; the editor values are the preview/fallback.
    config = json.loads(files["webblock/site-config.json"])
    blocks = {c["props"]["id"]: c for c in config["pages"]["/"]["content"]}
    assert blocks["menu-1"]["props"]["source"] == "database"
    assert blocks["location-1"]["type"] == "ContactInfo" and blocks["location-1"]["props"]["hours"] == "Mon–Sun 7:00–18:00"


def test_no_secret_ever_lands_in_the_repository(h: Harness) -> None:
    _, files = _deployed_site(h)
    blob = "\n".join(files.values())
    assert "PRIVATE KEY" not in blob
    assert "ghp_" not in blob and "github_pat_" not in blob
    assert re.search(r"DATABASE_URL=postgres://webblock_site:password@localhost", files[".env.example"])  # placeholder only
    assert ".env" in files[".gitignore"]


def test_migration_enables_row_level_security_for_every_table() -> None:
    sql = (SITE_DIR / "drizzle/0000_webblock_init.sql").read_text()
    tables = re.findall(r'CREATE TABLE IF NOT EXISTS "webblock"\."(\w+)"', sql)
    assert sorted(tables) == ["business_contacts", "business_profiles", "products", "site_settings"]
    array = re.search(r"ARRAY\[([^\]]+)\]", sql)
    assert array and sorted(re.findall(r"'(\w+)'", array.group(1))) == sorted(tables)
    assert "FORCE ROW LEVEL SECURITY" in sql and "current_setting(''app.tenant_id'', true)" in sql
    schema = (SITE_DIR / "src/db/schema.ts").read_text()
    for table in tables:
        assert f'webblock.table(\n  "{table}"' in schema or f'webblock.table("{table}"' in schema


def test_portal_session_is_scoped_to_one_site_and_owner(h: Harness) -> None:
    site_a, _ = _deployed_site(h)
    site_b = h.client.post("/v1/sites", json={"template_id": "artisan-shop", "profile": PROFILE}, headers=_hdr(h)).json()
    session = h.client.post(f"/v1/projects/{site_a['id']}/portal-session", headers=_hdr(h)).json()
    assert session["portal_url"] == f"http://localhost:3000/business#token={session['token']}"
    signer = h.container.signer()
    claims = signer.verify(session["token"], site_a["data_tenant_id"])
    assert claims["sub"] == "owner@sok.example" and claims["scope"] == "business_portal"
    # The token is useless for any other site.
    with pytest.raises(pyjwt.InvalidAudienceError):
        signer.verify(session["token"], site_b["data_tenant_id"])
    # Another WebBlock user cannot get a portal session for this site.
    assert h.client.post(f"/v1/projects/{site_a['id']}/portal-session", headers=_hdr(h, "other")).status_code == 404


def test_site_url_is_validated(h: Harness) -> None:
    site = h.client.post("/v1/sites", json={"template_id": "cafe", "profile": PROFILE}, headers=_hdr(h)).json()
    bad = h.client.patch(f"/v1/projects/{site['id']}", json={"site_url": "javascript:alert(1)"}, headers=_hdr(h))
    assert bad.status_code == 422
    ok = h.client.patch(f"/v1/projects/{site['id']}", json={"site_url": "https://sok-cafe.webblock.app/"}, headers=_hdr(h)).json()
    assert ok["site_url"] == "https://sok-cafe.webblock.app"
    session = h.client.post(f"/v1/projects/{site['id']}/portal-session", headers=_hdr(h)).json()
    assert session["portal_url"].startswith("https://sok-cafe.webblock.app/business#token=")


def test_business_types_and_profile_validation(h: Harness) -> None:
    types = h.client.get("/v1/business-types", headers=_hdr(h)).json()
    assert "Café" in types and "Salon" in types
    bad = h.client.post(
        "/v1/sites", json={"template_id": "cafe", "profile": {**PROFILE, "logo_url": "//evil.example/x.png"}}, headers=_hdr(h)
    )
    assert bad.status_code == 422


def test_existing_databases_get_tenant_ids_backfilled(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path}/old.db")
    db.create_all()
    with db.session() as s:
        s.execute(text("INSERT INTO tenants (id, name, created_at) VALUES ('t1', 't1', CURRENT_TIMESTAMP)"))
        s.add(Project(tenant_id="t1", name="old", repo_owner="o", repo_name="r"))
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE projects DROP COLUMN data_tenant_id"))
    db.create_all()  # simulates upgrading an existing installation
    with db.session() as s:
        value = s.execute(text("SELECT data_tenant_id FROM projects")).scalar_one()
    assert re.fullmatch(r"[0-9a-f-]{36}", value)


@pytest.mark.parametrize("has_manifest", [True, False], ids=["manifest", "deployed-before-manifests"])
def test_update_code_adds_backend_to_an_older_site_and_keeps_github_edits(
    h: Harness, monkeypatch: pytest.MonkeyPatch, has_manifest: bool
) -> None:
    from webblock_harness.services.pipeline import Pipeline

    real = Pipeline._site_files

    def old_site_files(self: Pipeline, project: Project, repo_name: str, config: dict[str, Any]) -> dict[str, str]:
        files = real(self, project, repo_name, config)
        files = {p: c for p, c in files.items() if not p.startswith(("src/db/", "src/app/business/", "webblock/site.json"))}
        files["package.json"] = '{"name": "old"}\n'
        files["README.md"] = "old readme\n"
        return files

    monkeypatch.setattr(Pipeline, "_site_files", old_site_files)
    site, _ = _deployed_site(h)  # deployed with the "old" WebBlock code
    monkeypatch.setattr(Pipeline, "_site_files", real)
    with h.container.db.session() as s:
        project = s.get(Project, site["id"])
        assert project is not None
        project.site_code_version = None
        if not has_manifest:
            project.site_files_manifest = None
    assert h.client.get(f"/v1/projects/{site['id']}", headers=_hdr(h)).json()["code_update_available"] is True

    owner, name = site["repository"].split("/")
    h.git.push_user_commit(owner, name, "main", {"README.md": "my own readme\n"})
    config_before = (
        h.git.repos[f"{owner}/{name}"].commits[h.git.repos[f"{owner}/{name}"].branches["main"]].files["webblock/site-config.json"]
    )

    job = h.client.post(f"/v1/projects/{site['id']}/update-code", headers=_hdr(h)).json()
    assert job["kind"] == "upgrade" and job["state"] == "succeeded", job["error"]
    assert job["outcome"] == "upgraded"
    repo = h.git.repos[f"{owner}/{name}"]
    files = repo.commits[repo.branches["main"]].files
    assert "src/app/business/page.tsx" in files and "src/db/schema.ts" in files
    assert json.loads(files["webblock/site.json"])["tenantId"] == site["data_tenant_id"]
    assert "drizzle-orm" in json.loads(files["package.json"])["dependencies"]  # unedited -> updated
    assert files["README.md"] == "my own readme\n"  # edited on GitHub -> kept
    assert files["webblock/site-config.json"] == config_before  # page content untouched
    events = h.client.get(f"/v1/jobs/{job['id']}/events", headers=_hdr(h)).json()
    kept = next(e for e in events if e["code"] == "site_code_updated")["data"]["kept_edited_files"]
    assert kept == ["README.md"]
    assert h.client.get(f"/v1/projects/{site['id']}", headers=_hdr(h)).json()["code_update_available"] is False

    again = h.client.post(f"/v1/projects/{site['id']}/update-code", headers=_hdr(h)).json()
    assert again["state"] == "succeeded" and again["outcome"] == "no_changes"


def test_update_code_requires_a_deployed_template_site(h: Harness) -> None:
    site = h.client.post("/v1/sites", json={"template_id": "cafe", "profile": PROFILE}, headers=_hdr(h)).json()
    assert h.client.post(f"/v1/projects/{site['id']}/update-code", headers=_hdr(h)).status_code == 409
    assert h.client.get(f"/v1/projects/{site['id']}", headers=_hdr(h)).json()["code_update_available"] is False

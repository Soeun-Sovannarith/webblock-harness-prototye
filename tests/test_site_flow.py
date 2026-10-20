"""The website-builder flow end to end:

onboarding form → template → edit in Puck (draft) → deploy (repository created + pushed) →
edit again → redeploy (only the changed content is committed) — including edits made directly on GitHub.

"Deployed" means the commit is on the default branch of the client repository in the organisation and the
committed site configuration is verified against the published revision (DEPLOY_PROVIDER=github_push).
"""

from __future__ import annotations

import copy
import difflib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import ORG, Harness, build_harness
from webblock_harness.integrations.git import RepoRef
from webblock_harness.registry.registry import load_base_registry
from webblock_harness.templates.catalog import TEMPLATES
from webblock_harness.templates.scaffold import SITE_DIR, render_scaffold

CONFIG = "webblock/site-config.json"
PROFILE = {
    "business_name": "Kampot Clay",
    "tagline": "Handmade pottery from Kampot",
    "description": "Wheel-thrown cups, bowls and vases made with local river clay.",
    "email": "hello@kampotclay.example",
    "phone": "+855 12 345 678",
    "currency": "USD",
    "products": [
        {"name": "Lotus Tea Cup", "description": "Celadon glaze.", "price": 12},
        {"name": "Mekong Bowl", "description": "River-blue glaze.", "price": 38},
    ],
}


@pytest.fixture
def site_harness(tmp_path: Path) -> Iterator[Harness]:
    with build_harness(
        tmp_path, register=False, CONFIG_DELIVERY_MODE="commit", DEPLOY_PROVIDER="github_push", DEV_LOGIN_ENABLED="true"
    ) as h:
        token = h.client.post("/v1/dev/session", json={"email": "owner@kampotclay.example"}).json()["token"]
        h.tokens["owner"] = token
        yield h


def _h(h: Harness, who: str = "owner") -> dict[str, str]:
    return {"Authorization": f"Bearer {h.tokens[who]}"}


def _create_site(h: Harness, template: str = "artisan-shop") -> dict[str, Any]:
    resp = h.client.post("/v1/sites", json={"template_id": template, "profile": PROFILE}, headers=_h(h))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _edit_draft(h: Harness, pid: str, mutate: Any) -> dict[str, Any]:
    draft = h.client.get(f"/v1/projects/{pid}/draft", headers=_h(h)).json()
    config = copy.deepcopy(draft["config"])
    mutate(config)
    resp = h.client.put(
        f"/v1/projects/{pid}/draft", json={"config": config, "expected_draft_revision_id": draft["id"]}, headers=_h(h)
    )
    assert resp.status_code == 200, resp.text
    return config


def _deploy(h: Harness, pid: str) -> dict[str, Any]:
    resp = h.client.post(f"/v1/projects/{pid}/deploy", headers=_h(h))
    assert resp.status_code == 202, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _component(config: dict[str, Any], cid: str, page: str = "/") -> dict[str, Any]:
    return next(c for c in config["pages"][page]["content"] if c["props"]["id"] == cid)


def _repo(h: Harness, name: str) -> Any:
    return h.git.repos[f"{ORG}/{name}"]


def _repo_config(h: Harness, name: str) -> dict[str, Any]:
    r = _repo(h, name)
    return json.loads(r.commits[r.branches["main"]].files[CONFIG])  # type: ignore[no-any-return]


def _changed_lines(h: Harness, name: str, old_sha: str, new_sha: str) -> list[str]:
    r = _repo(h, name)
    before = r.commits[old_sha].files[CONFIG].splitlines()
    after = r.commits[new_sha].files[CONFIG].splitlines()
    return [ln for ln in difflib.unified_diff(before, after, lineterm="", n=0) if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]


# --------------------------------------------------------------------------


def test_dev_login_is_disabled_by_default(harness: Harness) -> None:
    assert harness.client.post("/v1/dev/session", json={"email": "a@b.co"}).status_code == 404


def test_templates_are_listed_and_valid(site_harness: Harness) -> None:
    templates = site_harness.client.get("/v1/templates", headers=_h(site_harness)).json()
    assert {t["id"] for t in templates} == set(TEMPLATES)
    assert all(t["preview"]["pages"]["/"]["content"] for t in templates)


def test_full_flow_form_template_edit_deploy_edit_redeploy(site_harness: Harness) -> None:
    h = site_harness
    # 1. Onboarding form + template → project; nothing is pushed yet.
    site = _create_site(h)
    pid, repo_name = site["id"], site["repository"].split("/")[1]
    assert repo_name == "kampot-clay-site"
    assert site["repository_created"] is False and site["deployed"] is False
    assert f"{ORG}/{repo_name}" not in h.git.repos
    draft = h.client.get(f"/v1/projects/{pid}/draft", headers=_h(h)).json()["config"]
    assert _component(draft, "hero-1")["props"]["heading"] == "Handmade pottery from Kampot"
    assert [p["name"] for p in _component(draft, "gallery-1")["props"]["products"]] == ["Lotus Tea Cup", "Mekong Bowl"]

    # Content changes before the first deploy are made in the editor, not as change requests.
    early = h.client.post(f"/v1/projects/{pid}/jobs", json={"request": "Change the price from $12 to $15"}, headers=_h(h))
    assert early.status_code == 409 and early.json()["error"]["code"] == "not_deployed"

    # 2. Edit in Puck (saved as a draft).
    _edit_draft(h, pid, lambda c: _component(c, "hero-1")["props"].update(heading="Clay from the Mekong, shaped by hand"))

    # 3. Deploy: repository created in the organisation and the full site pushed.
    job = _deploy(h, pid)
    assert job["state"] == "succeeded", job["error"]
    assert job["kind"] == "provision" and job["outcome"] == "provisioned"
    files = _repo(h, repo_name).commits[_repo(h, repo_name).branches["main"]].files
    assert set(render_scaffold("Kampot Clay", repo_name)) <= set(files)
    for spec in load_base_registry().components.values():
        assert spec.source_path in files  # every registered component ships with the site
    assert _component(_repo_config(h, repo_name), "hero-1")["props"]["heading"] == "Clay from the Mekong, shaped by hand"
    site = h.client.get(f"/v1/projects/{pid}", headers=_h(h)).json()
    assert site["repository_created"] and site["deployed"]
    first_sha = job["merged_commit_sha"]
    snaps = h.client.get(f"/v1/projects/{pid}/snapshots", headers=_h(h)).json()
    assert snaps[0]["commit_sha"] == first_sha
    deps = h.client.get(f"/v1/projects/{pid}/deployments", headers=_h(h)).json()
    assert deps[0]["provider"] == "github_push" and deps[0]["status"] == "verified"
    assert deps[0]["url"] == f"https://github.com/{ORG}/{repo_name}/commit/{first_sha}"

    # Nothing changed → nothing to deploy.
    assert h.client.post(f"/v1/projects/{pid}/deploy", headers=_h(h)).json()["error"]["code"] == "nothing_to_deploy"

    # 4. Edit again (one price) and redeploy: only that content changes in the repository.
    _edit_draft(h, pid, lambda c: _component(c, "gallery-1")["props"]["products"][0].update(price=15))
    job2 = _deploy(h, pid)
    assert job2["state"] == "succeeded", job2["error"]
    assert job2["kind"] == "change" and job2["path"] == "runtime_config"
    assert [c["kind"] for c in job2["plan"]["changes"]] == ["content_update"]
    second_sha = job2["merged_commit_sha"]
    assert h.git.changed_files(RepoRef(ORG, repo_name), first_sha, second_sha) == [CONFIG]
    # The git diff is exactly the edited value.
    assert [ln[0] + ln[1:].strip() for ln in _changed_lines(h, repo_name, first_sha, second_sha)] == [
        '-"price": 12',
        '+"price": 15',
    ]
    assert "Update site content" in h.git.commit_message(RepoRef(ORG, repo_name), second_sha)
    assert h.planner.calls == []  # no code generation for content edits


def test_edits_made_on_github_are_kept_on_redeploy(site_harness: Harness) -> None:
    h = site_harness
    site = _create_site(h)
    pid, repo_name = site["id"], site["repository"].split("/")[1]
    assert _deploy(h, pid)["state"] == "succeeded"

    # A developer edits the content file directly on GitHub…
    on_github = _repo_config(h, repo_name)
    _component(on_github, "hero-1")["props"]["subheading"] = "Edited on GitHub"
    h.git.push_user_commit(ORG, repo_name, "main", {CONFIG: json.dumps(on_github, indent=2, sort_keys=True) + "\n"})
    # …while the owner (whose editor still shows the old version) changes a different field.
    _edit_draft(h, pid, lambda c: _component(c, "testimonial-1")["props"].update(author="Sokha, Phnom Penh"))

    job = _deploy(h, pid)
    assert job["state"] == "succeeded", job["error"]
    live = _repo_config(h, repo_name)
    assert _component(live, "hero-1")["props"]["subheading"] == "Edited on GitHub"
    assert _component(live, "testimonial-1")["props"]["author"] == "Sokha, Phnom Penh"
    events = [e["code"] for e in h.client.get(f"/v1/jobs/{job['id']}/events", headers=_h(h)).json()]
    assert "reconciled_with_repository" in events


def test_same_value_changed_on_github_and_in_editor_is_a_conflict(site_harness: Harness) -> None:
    h = site_harness
    site = _create_site(h)
    pid, repo_name = site["id"], site["repository"].split("/")[1]
    assert _deploy(h, pid)["state"] == "succeeded"
    on_github = _repo_config(h, repo_name)
    _component(on_github, "gallery-1")["props"]["products"][0]["price"] = 20
    h.git.push_user_commit(ORG, repo_name, "main", {CONFIG: json.dumps(on_github, indent=2, sort_keys=True) + "\n"})
    head = _repo(h, repo_name).branches["main"]

    _edit_draft(h, pid, lambda c: _component(c, "gallery-1")["props"]["products"][0].update(price=15))
    job = _deploy(h, pid)
    assert job["state"] == "failed" and job["error"]["code"] == "repo_conflict"
    assert _repo(h, repo_name).branches["main"] == head  # GitHub edit untouched
    assert _component(_repo_config(h, repo_name), "gallery-1")["props"]["products"][0]["price"] == 20


def test_sync_imports_github_edits_and_rebases_the_draft(site_harness: Harness) -> None:
    h = site_harness
    site = _create_site(h)
    pid, repo_name = site["id"], site["repository"].split("/")[1]
    assert h.client.post(f"/v1/projects/{pid}/sync", headers=_h(h)).json()["status"] == "not_deployed"
    assert _deploy(h, pid)["state"] == "succeeded"
    assert h.client.post(f"/v1/projects/{pid}/sync", headers=_h(h)).json()["status"] == "in_sync"

    _edit_draft(h, pid, lambda c: _component(c, "testimonial-1")["props"].update(quote="Unpublished editor change"))
    on_github = _repo_config(h, repo_name)
    _component(on_github, "hero-1")["props"]["heading"] = "Heading edited on GitHub"
    h.git.push_user_commit(ORG, repo_name, "main", {CONFIG: json.dumps(on_github, indent=2) + "\n"})

    result = h.client.post(f"/v1/projects/{pid}/sync", headers=_h(h)).json()
    assert result["status"] == "imported" and result["draft"] == "rebased"
    draft = h.client.get(f"/v1/projects/{pid}/draft", headers=_h(h)).json()["config"]
    assert _component(draft, "hero-1")["props"]["heading"] == "Heading edited on GitHub"
    assert _component(draft, "testimonial-1")["props"]["quote"] == "Unpublished editor change"

    job = _deploy(h, pid)
    assert job["state"] == "succeeded", job["error"]
    live = _repo_config(h, repo_name)
    assert _component(live, "hero-1")["props"]["heading"] == "Heading edited on GitHub"
    assert _component(live, "testimonial-1")["props"]["quote"] == "Unpublished editor change"


def test_invalid_github_edit_is_not_imported(site_harness: Harness) -> None:
    h = site_harness
    site = _create_site(h)
    pid, repo_name = site["id"], site["repository"].split("/")[1]
    assert _deploy(h, pid)["state"] == "succeeded"
    on_github = _repo_config(h, repo_name)
    _component(on_github, "hero-1")["props"]["ctaHref"] = "javascript:alert(1)"
    h.git.push_user_commit(ORG, repo_name, "main", {CONFIG: json.dumps(on_github) + "\n"})
    result = h.client.post(f"/v1/projects/{pid}/sync", headers=_h(h)).json()
    assert result["status"] == "invalid_repo_config"
    assert "unsafe_url" in {i["code"] for i in result["issues"]}


def test_sites_are_tenant_isolated(site_harness: Harness) -> None:
    h = site_harness
    site = _create_site(h)
    h.tokens["other"] = h.client.post("/v1/dev/session", json={"email": "someone@else.example"}).json()["token"]
    for method, url in [
        ("get", f"/v1/projects/{site['id']}"),
        ("get", f"/v1/projects/{site['id']}/draft"),
        ("post", f"/v1/projects/{site['id']}/deploy"),
        ("post", f"/v1/projects/{site['id']}/sync"),
    ]:
        assert getattr(h.client, method)(url, headers=_h(h, "other")).status_code == 404
    assert h.client.get("/v1/projects", headers=_h(h, "other")).json() == []


def test_repository_names_never_collide(site_harness: Harness) -> None:
    h = site_harness
    h.git.create_repo(ORG, "kampot-clay-site", {"README.md": "someone else's"})
    a = _create_site(h)
    b = _create_site(h)
    assert a["repository"] == f"{ORG}/kampot-clay-site-2"
    assert b["repository"] == f"{ORG}/kampot-clay-site-3"


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_every_template_deploys(site_harness: Harness, template: str) -> None:
    site = _create_site(site_harness, template)
    job = _deploy(site_harness, site["id"])
    assert job["state"] == "succeeded", job["error"]


def test_scaffold_matches_registry() -> None:
    """The site's components and slot declarations must match the registry the harness validates against."""
    registry = load_base_registry()
    index = (SITE_DIR / "src/components/webblock/index.ts").read_text()
    for spec in registry.components.values():
        assert spec.source_path and (SITE_DIR / spec.source_path).exists()
        assert re.search(rf"\b{spec.name}\b", index)
        slots = [k for k, f in spec.fields.items() if f.type == "slot"]
        if slots:
            declared = re.search(rf"{spec.name}: \[([^\]]*)\]", index)
            assert declared and sorted(re.findall(r'"(\w+)"', declared.group(1))) == sorted(slots)

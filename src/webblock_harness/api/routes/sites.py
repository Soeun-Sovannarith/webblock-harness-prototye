"""The website-builder flow: onboarding form → template → Puck editor → deploy → edit → redeploy."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import jwt
from fastapi import APIRouter, Header, Request, status

from webblock_harness.api.deps import ContainerDep, PrincipalDep, WorkerDep
from webblock_harness.api.schemas import DevSession, JobView, ProjectUpdate, ProjectView, SiteCreate
from webblock_harness.models import Job
from webblock_harness.puck.normalize import normalize_site
from webblock_harness.security.auth import Role
from webblock_harness.services.errors import NotFound
from webblock_harness.services.jobs import JobService
from webblock_harness.services.projects import ProjectService, require
from webblock_harness.templates.catalog import BUSINESS_TYPES, TEMPLATES, SiteProfile

router = APIRouter(tags=["sites"])


@router.get("/v1/templates")
def list_templates(p: PrincipalDep) -> list[dict[str, Any]]:
    """Templates the client can start from, with a preview configuration built from sample data."""
    sample = SiteProfile(business_name="Your Business", tagline="", description="")
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "theme": t.theme,
            "best_for": t.best_for,
            "preview": normalize_site(t.build(sample)),
        }
        for t in TEMPLATES.values()
    ]


@router.get("/v1/business-types")
def business_types(p: PrincipalDep) -> list[str]:
    return BUSINESS_TYPES


@router.post("/v1/sites", status_code=status.HTTP_201_CREATED, response_model=ProjectView)
def create_site(body: SiteCreate, c: ContainerDep, p: PrincipalDep) -> ProjectView:
    """Create a website from the onboarding form and a template. Nothing is pushed until the first deploy."""
    with c.db.session() as s:
        return ProjectView.of(ProjectService(c).create_site(s, p, body.profile, body.template_id))


@router.post("/v1/projects/{project_id}/deploy", status_code=status.HTTP_202_ACCEPTED, response_model=JobView)
def deploy(
    project_id: str,
    c: ContainerDep,
    w: WorkerDep,
    p: PrincipalDep,
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> JobView:
    """Deploy the editor draft. First deploy creates the GitHub repository; later deploys commit only what changed."""
    with c.db.session() as s:
        job, created = JobService(c).create_deploy_job(s, p, project_id, idempotency_key)
        job_id = job.id
    if created:
        w.submit(job_id)
    with c.db.session() as s:
        return JobView.of(s.get(Job, job_id))  # type: ignore[arg-type]


@router.post("/v1/projects/{project_id}/update-code", status_code=status.HTTP_202_ACCEPTED, response_model=JobView)
def update_code(
    project_id: str,
    c: ContainerDep,
    w: WorkerDep,
    p: PrincipalDep,
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> JobView:
    """Bring a deployed site up to the latest WebBlock site code (e.g. add the Drizzle backend and Business Portal)."""
    with c.db.session() as s:
        job, created = JobService(c).create_upgrade_job(s, p, project_id, idempotency_key)
        job_id = job.id
    if created:
        w.submit(job_id)
    with c.db.session() as s:
        return JobView.of(s.get(Job, job_id))  # type: ignore[arg-type]


@router.patch("/v1/projects/{project_id}", response_model=ProjectView)
def update_project(project_id: str, body: ProjectUpdate, c: ContainerDep, p: PrincipalDep) -> ProjectView:
    with c.db.session() as s:
        require(p, Role.EDITOR)
        project = ProjectService(c).get(s, p, project_id)
        if "site_url" in body.model_fields_set:
            project.site_url = body.site_url.rstrip("/") if body.site_url else None
        return ProjectView.of(project)


@router.post("/v1/projects/{project_id}/portal-session")
def portal_session(project_id: str, c: ContainerDep, p: PrincipalDep) -> dict[str, Any]:
    """Sign the owner into this site's Business Portal (products, business info, contacts).

    Returns a short-lived token valid for this one site only; the portal URL carries it in the fragment
    (never sent to servers or logs) and the portal removes it from the address bar immediately.
    """
    with c.db.session() as s:
        require(p, Role.EDITOR)
        project = ProjectService(c).get(s, p, project_id)
        token, exp = c.signer().issue(project.data_tenant_id, p.user_id)
        site_url = project.site_url or c.settings.default_site_url
        return {
            "token": token,
            "expires_at": exp,
            "site_url": site_url,
            "portal_url": f"{site_url}/business#token={token}",
            "repository_created": project.repo_provisioned,
        }


@router.post("/v1/projects/{project_id}/sync")
def sync(project_id: str, c: ContainerDep, p: PrincipalDep) -> dict[str, Any]:
    """Import edits made directly to the site configuration on GitHub (call before opening the editor)."""
    with c.db.session() as s:
        return ProjectService(c).sync_from_repository(s, p, project_id)


@router.post("/v1/dev/session")
def dev_session(body: DevSession, request: Request, c: ContainerDep) -> dict[str, Any]:
    """Local development sign-in for the bundled web app (DEV_LOGIN_ENABLED=true only).

    Production identities are issued by the WebBlock auth service; this endpoint does not exist there.
    """
    s = c.settings
    if not s.dev_login_enabled or s.auth_jwt_secret is None:
        raise NotFound("not_found", "Not found.")
    email = body.email.strip().lower()
    tenant = "dev-" + hashlib.sha256(email.encode()).hexdigest()[:16]
    exp = int(time.time()) + 8 * 3600
    claims: dict[str, Any] = {"sub": email, s.auth_tenant_claim: tenant, s.auth_roles_claim: ["editor", "owner"], "exp": exp}
    if s.auth_jwt_issuer:
        claims["iss"] = s.auth_jwt_issuer
    if s.auth_jwt_audience:
        claims["aud"] = s.auth_jwt_audience
    token = jwt.encode(claims, s.auth_jwt_secret.get_secret_value(), algorithm=s.auth_jwt_algorithms[0])
    return {"token": token, "user_id": email, "tenant_id": tenant, "expires_at": exp}

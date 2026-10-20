from __future__ import annotations

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from webblock_harness.api.deps import ContainerDep, PrincipalDep, SessionDep
from webblock_harness.services.projects import ProjectService

router = APIRouter(tags=["public"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(s: SessionDep, c: ContainerDep) -> dict[str, object]:
    s.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "git_provider": c.settings.git_provider.value,
        "github_org_configured": bool(c.settings.github_org),
        "deploy_provider": c.deploy.name,
        "llm_available": c.planner.available,
        "config_delivery_mode": c.settings.config_delivery_mode.value,
    }


@router.get("/v1/me")
def me(p: PrincipalDep) -> dict[str, object]:
    return {"user_id": p.user_id, "tenant_id": p.tenant_id, "roles": sorted(r.value for r in p.roles)}


@router.get("/v1/public/sites/{site_key}/config")
def site_config(site_key: str, s: SessionDep, c: ContainerDep, if_none_match: str | None = Header(default=None)) -> Response:
    """Runtime configuration delivery: the published (never the draft) configuration of a site.

    Public by design (it is the site's public content). The unguessable ``site_key``
    is not an authorisation boundary for anything else.
    """
    config, digest = ProjectService(c).public_config(s, site_key)
    etag = f'"{digest}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=0, must-revalidate", "X-Config-Hash": digest}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse({"config_hash": digest, "config": config}, headers=headers)

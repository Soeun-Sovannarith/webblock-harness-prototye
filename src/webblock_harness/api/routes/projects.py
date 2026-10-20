from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query, status
from sqlalchemy import select

from webblock_harness.analyzer.analyzer import AnalysisInput, ChangeAnalyzer, SourceContext
from webblock_harness.analyzer.llm import PlannerError
from webblock_harness.api.deps import ContainerDep, PrincipalDep, SessionDep, WorkerDep
from webblock_harness.api.schemas import (
    ChangeRequest,
    DeploymentView,
    DraftUpdate,
    JobView,
    ProjectCreate,
    ProjectView,
    RevisionView,
    RollbackBody,
)
from webblock_harness.domain import ChangePlan
from webblock_harness.integrations.git import GitError
from webblock_harness.models import Deployment, Job, Snapshot
from webblock_harness.puck.normalize import ConfigFormatError
from webblock_harness.security.auth import Role
from webblock_harness.services.errors import NotFound, Unavailable, Unprocessable
from webblock_harness.services.jobs import JobService
from webblock_harness.services.projects import ProjectService, require, snapshot_view

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectView)
def register_project(body: ProjectCreate, c: ContainerDep, p: PrincipalDep) -> ProjectView:
    """Associate a repository in the WebBlock GitHub organisation with a new project (admin only)."""
    with c.db.session() as s:
        project = ProjectService(c).register(s, p, name=body.name, repo_name=body.repo_name, initial_config=body.initial_config)
        return ProjectView.of(project)


@router.get("", response_model=list[ProjectView])
def list_projects(s: SessionDep, c: ContainerDep, p: PrincipalDep) -> list[ProjectView]:
    return [ProjectView.of(x) for x in ProjectService(c).list_projects(s, p)]


@router.get("/{project_id}", response_model=ProjectView)
def get_project(project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> ProjectView:
    return ProjectView.of(ProjectService(c).get(s, p, project_id))


@router.get("/{project_id}/context")
def project_context(
    project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep, include_files: bool = True
) -> dict[str, Any]:
    """Inspect the current configuration, registry, source revision and last snapshot."""
    return ProjectService(c).context(s, p, project_id, include_files=include_files)


@router.get("/{project_id}/registry")
def project_registry(project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> dict[str, Any]:
    svc = ProjectService(c)
    registry = svc.registry_for(s, svc.get(s, p, project_id))
    return {
        "hash": registry.hash,
        "root": registry.root.model_dump(mode="json"),
        "components": [spec.model_dump(mode="json") for spec in registry.components.values()],
    }


# -- configuration: drafts and revisions -----------------------------------------


@router.get("/{project_id}/draft", response_model=RevisionView)
def get_draft(project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> RevisionView:
    svc = ProjectService(c)
    project = svc.get(s, p, project_id)
    rev = svc.revision(s, project, project.draft_revision_id or project.published_revision_id)
    return RevisionView.of(rev, svc.load(project, rev))


@router.put("/{project_id}/draft", response_model=RevisionView)
def save_draft(project_id: str, body: DraftUpdate, c: ContainerDep, p: PrincipalDep) -> RevisionView:
    """Save the Puck editor draft. Drafts never affect the published site until a job publishes them."""
    with c.db.session() as s:
        svc = ProjectService(c)
        rev, _ = svc.save_draft(s, p, project_id, body.config, body.expected_draft_revision_id)
        return RevisionView.of(rev)


@router.get("/{project_id}/revisions", response_model=list[RevisionView])
def list_revisions(
    project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep, limit: int = Query(default=50, ge=1, le=200)
) -> list[RevisionView]:
    svc = ProjectService(c)
    return [RevisionView.of(r) for r in svc.list_revisions(s, svc.get(s, p, project_id), limit)]


@router.get("/{project_id}/revisions/{revision_id}", response_model=RevisionView)
def get_revision(project_id: str, revision_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> RevisionView:
    """Fetch any revision's configuration (used by the preview renderer)."""
    svc = ProjectService(c)
    project = svc.get(s, p, project_id)
    rev = svc.revision(s, project, revision_id)
    return RevisionView.of(rev, svc.load(project, rev))


# -- analysis and jobs --------------------------------------------------------------


@router.post("/{project_id}/analyze", response_model=ChangePlan)
def analyze(project_id: str, body: ChangeRequest, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> ChangePlan:
    """Dry run: produce the structured change plan without creating a job or changing anything."""
    require(p, Role.VIEWER)
    svc = ProjectService(c)
    project = svc.get(s, p, project_id)
    _, base = svc.published(s, project)
    repo = svc.repo(project)
    try:
        head = c.git.get_branch_head(repo, project.default_branch)
        files = c.git.list_files(repo, head) if head else []
    except GitError as exc:
        raise Unavailable("git_unavailable", exc.message) from exc
    proposed = None
    if body.use_draft:
        proposed = svc.load(project, svc.revision(s, project, project.draft_revision_id))
    elif body.proposed_config is not None:
        proposed = body.proposed_config
    from pydantic import TypeAdapter, ValidationError

    from webblock_harness.domain import PuckOp

    try:
        ops = TypeAdapter(list[PuckOp]).validate_python(body.operations) if body.operations is not None else None
        return ChangeAnalyzer(c.planner).analyze(
            AnalysisInput(
                base_config=base,
                registry=svc.registry_for(s, project),
                request_text=body.request or "",
                proposed_config=proposed,
                explicit_ops=ops,
                source=SourceContext(
                    head_sha=head,
                    files=files,
                    history=(lambda path: c.git.file_history(repo, path, head, 5)) if head else None,
                    harness_author_email=c.settings.harness_commit_author_email,
                ),
                auto_approve=c.auto_approve,
                required_checks=c.settings.required_checks_for,
            )
        )
    except (ConfigFormatError, ValidationError) as exc:
        raise Unprocessable("invalid_input", str(exc)[:500]) from exc
    except PlannerError as exc:
        raise Unavailable(exc.code, exc.message) from exc


@router.post("/{project_id}/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobView)
def create_job(
    project_id: str,
    body: ChangeRequest,
    c: ContainerDep,
    w: WorkerDep,
    p: PrincipalDep,
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> JobView:
    """Submit a change. The job runs asynchronously; poll GET /v1/jobs/{id} or its events."""
    with c.db.session() as s:
        job, created = JobService(c).create_change_job(
            s,
            p,
            project_id,
            request_text=body.request,
            proposed_config=body.proposed_config,
            operations=body.operations,
            use_draft=body.use_draft,
            idempotency_key=idempotency_key,
        )
        job_id = job.id
    if created:
        w.submit(job_id)
    with c.db.session() as s:
        return JobView.of(s.get(Job, job_id))  # type: ignore[arg-type]


@router.get("/{project_id}/jobs", response_model=list[JobView])
def list_jobs(
    project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep, limit: int = Query(default=50, ge=1, le=200)
) -> list[JobView]:
    return [JobView.of(j) for j in JobService(c).list_jobs(s, p, project_id, limit)]


# -- snapshots, deployments, rollback ---------------------------------------------------


@router.get("/{project_id}/snapshots")
def list_snapshots(project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> list[dict[str, Any]]:
    project = ProjectService(c).get(s, p, project_id)
    rows = s.scalars(
        select(Snapshot)
        .where(Snapshot.project_id == project.id, Snapshot.tenant_id == p.tenant_id)
        .order_by(Snapshot.created_at.desc())
    )
    return [snapshot_view(x) | {"is_current": x.id == project.last_successful_snapshot_id} for x in rows]


@router.get("/{project_id}/snapshots/{snapshot_id}")
def get_snapshot(project_id: str, snapshot_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> dict[str, Any]:
    project = ProjectService(c).get(s, p, project_id)
    snap = s.scalar(
        select(Snapshot).where(Snapshot.id == snapshot_id, Snapshot.project_id == project.id, Snapshot.tenant_id == p.tenant_id)
    )
    if snap is None:
        raise NotFound("snapshot_not_found", "Snapshot not found.")
    return snapshot_view(snap) | {"manifest": c.store.get_json(p.tenant_id, snap.manifest_key)}


@router.get("/{project_id}/deployments", response_model=list[DeploymentView])
def list_deployments(project_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> list[DeploymentView]:
    project = ProjectService(c).get(s, p, project_id)
    rows = s.scalars(
        select(Deployment)
        .where(Deployment.project_id == project.id, Deployment.tenant_id == p.tenant_id)
        .order_by(Deployment.created_at.desc())
    )
    return [DeploymentView.of(d) for d in rows]


@router.post("/{project_id}/rollback", status_code=status.HTTP_202_ACCEPTED, response_model=JobView)
def rollback(
    project_id: str,
    body: RollbackBody,
    c: ContainerDep,
    w: WorkerDep,
    p: PrincipalDep,
    idempotency_key: str | None = Header(default=None, max_length=200),
) -> JobView:
    """Restore a known-good snapshot (source tree and configuration) through the normal deploy + verify flow."""
    with c.db.session() as s:
        job, created = JobService(c).create_rollback_job(s, p, project_id, body.snapshot_id, idempotency_key)
        job_id = job.id
    if created:
        w.submit(job_id)
    with c.db.session() as s:
        return JobView.of(s.get(Job, job_id))  # type: ignore[arg-type]

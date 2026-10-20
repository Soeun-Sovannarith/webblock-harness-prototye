"""Request/response models. Responses never contain credentials or other tenants' data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from webblock_harness.domain import ChangePlan
from webblock_harness.models import ConfigRevision, Deployment, Job, JobEvent, Project
from webblock_harness.templates.catalog import SiteProfile


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    repo_name: str = Field(min_length=1, max_length=100, description="Repository name inside the WebBlock GitHub organisation.")
    initial_config: dict[str, Any] | None = Field(
        default=None, description="The template's Puck configuration (site or single page)."
    )


class ProjectView(BaseModel):
    id: str
    name: str
    site_key: str
    repository: str
    repository_url: str
    repository_created: bool
    deployed: bool
    template_id: str | None
    default_branch: str
    site_url: str | None
    data_tenant_id: str
    # True when the site was deployed with older WebBlock site code (e.g. before the Business Portal existed).
    code_update_available: bool
    pointers: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, p: Project) -> ProjectView:
        from webblock_harness.services.projects import pointers
        from webblock_harness.templates.scaffold import site_code_version

        return cls(
            id=p.id,
            name=p.name,
            site_key=p.site_key,
            repository=f"{p.repo_owner}/{p.repo_name}",
            repository_url=f"https://github.com/{p.repo_owner}/{p.repo_name}",
            repository_created=p.repo_provisioned,
            deployed=p.last_successful_snapshot_id is not None,
            template_id=p.template_id,
            default_branch=p.default_branch,
            site_url=p.site_url,
            data_tenant_id=p.data_tenant_id,
            code_update_available=bool(p.repo_provisioned and p.template_id and p.site_code_version != site_code_version()),
            pointers=pointers(p),
            created_at=p.created_at,
        )


class SiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    profile: SiteProfile


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_url: str | None = Field(
        default=None,
        max_length=300,
        pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$",
        description="Where the generated site runs (e.g. http://localhost:3000 or its WebBlock subdomain).",
    )


class DevSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]
    expected_draft_revision_id: str | None = Field(
        description="The draft revision you loaded; stale saves are rejected with 409."
    )


class RevisionView(BaseModel):
    id: str
    number: int
    status: str
    content_hash: str
    parent_revision_id: str | None
    job_id: str | None
    created_by: str
    created_at: datetime
    validation: dict[str, Any] | None = None
    config: dict[str, Any] | None = None

    @classmethod
    def of(cls, r: ConfigRevision, config: dict[str, Any] | None = None) -> RevisionView:
        return cls(
            id=r.id,
            number=r.number,
            status=r.status,
            content_hash=r.content_hash,
            parent_revision_id=r.parent_revision_id,
            job_id=r.job_id,
            created_by=r.created_by,
            created_at=r.created_at,
            validation=r.validation,
            config=config,
        )


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str | None = Field(default=None, max_length=4000, description="Natural-language change request.")
    proposed_config: dict[str, Any] | None = Field(default=None, description="Full proposed Puck configuration from the editor.")
    operations: list[dict[str, Any]] | None = Field(default=None, description="Explicit targeted Puck operations.")
    use_draft: bool = Field(default=False, description="Publish the current editor draft.")


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_hash: str = Field(description="Hash of the plan you reviewed; approval is bound to it.")


class ClarifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[str] = Field(min_length=1, max_length=10)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str


class JobView(BaseModel):
    id: str
    project_id: str
    kind: str
    state: str
    path: str | None
    outcome: str | None
    request: str | None
    plan: ChangePlan | None
    plan_hash: str | None
    requires_approval: bool
    approved_by: str | None
    approved_at: datetime | None
    clarification_answers: list[str]
    base_revision_id: str | None
    base_commit_sha: str | None
    proposed_revision_id: str | None
    branch: str | None
    pr_number: int | None
    commit_sha: str | None
    merged_commit_sha: str | None
    deployment_id: str | None
    snapshot_id: str | None
    validation: dict[str, Any] | None
    error: dict[str, Any] | None
    attempts: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, j: Job) -> JobView:
        return cls(
            id=j.id,
            project_id=j.project_id,
            kind=j.kind,
            state=j.state,
            path=j.path,
            outcome=j.outcome,
            request=j.request_text,
            plan=ChangePlan.model_validate(j.plan) if j.plan else None,
            plan_hash=j.plan_hash,
            requires_approval=j.requires_approval,
            approved_by=j.approved_by,
            approved_at=j.approved_at,
            clarification_answers=list(j.clarification_answers or []),
            base_revision_id=j.base_revision_id,
            base_commit_sha=j.base_commit_sha,
            proposed_revision_id=j.proposed_revision_id,
            branch=j.branch,
            pr_number=j.pr_number,
            commit_sha=j.commit_sha,
            merged_commit_sha=j.merged_commit_sha,
            deployment_id=j.deployment_id,
            snapshot_id=j.snapshot_id,
            validation=j.validation,
            error=j.error,
            attempts=j.attempts,
            created_by=j.created_by,
            created_at=j.created_at,
            updated_at=j.updated_at,
            finished_at=j.finished_at,
        )


class EventView(BaseModel):
    id: int
    code: str
    level: str
    message: str
    from_state: str | None
    to_state: str | None
    data: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, e: JobEvent) -> EventView:
        return cls(
            id=e.id,
            code=e.code,
            level=e.level,
            message=e.message,
            from_state=e.from_state,
            to_state=e.to_state,
            data=e.data,
            created_at=e.created_at,
        )


class DeploymentView(BaseModel):
    id: str
    job_id: str
    attempt: int
    provider: str
    external_id: str | None
    commit_sha: str | None
    config_revision_id: str | None
    status: str
    url: str | None
    details: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, d: Deployment) -> DeploymentView:
        return cls(
            id=d.id,
            job_id=d.job_id,
            attempt=d.attempt,
            provider=d.provider,
            external_id=d.external_id,
            commit_sha=d.commit_sha,
            config_revision_id=d.config_revision_id,
            status=d.status,
            url=d.url,
            details=d.details,
            created_at=d.created_at,
            finished_at=d.finished_at,
        )

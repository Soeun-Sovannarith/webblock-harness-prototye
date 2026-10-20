"""Relational schema for harness metadata.

Large payloads (Puck configurations, code proposals, diffs, CI summaries) live in
the artifact store; tables keep identifiers, hashes and storage keys only.
Every tenant-owned table carries ``tenant_id`` so queries can always be scoped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from webblock_harness.db import Base, utcnow


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("repo_owner", "repo_name", name="uq_project_repo"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prj"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    site_key: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: uuid.uuid4().hex)
    repo_owner: Mapped[str] = mapped_column(String(100))
    repo_name: Mapped[str] = mapped_column(String(100))
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    # False until the first deploy creates the repository and pushes the site scaffold.
    repo_provisioned: Mapped[bool] = mapped_column(default=True)
    template_id: Mapped[str | None] = mapped_column(String(50))
    # Tenant id of this website's rows in the shared business database (also in the repo's webblock/site.json).
    data_tenant_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid.uuid4()))
    site_url: Mapped[str | None] = mapped_column(String(300))
    # Version of the WebBlock site code last pushed (see templates.scaffold.site_code_version); None = before versioning.
    site_code_version: Mapped[str | None] = mapped_column(String(64))
    # sha256 of every code file as WebBlock last wrote it, so later code updates can tell untouched files from edited ones.
    site_files_manifest: Mapped[dict[str, str] | None] = mapped_column(JSON)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Head of the default branch as last observed/produced by the harness.
    source_commit_sha: Mapped[str | None] = mapped_column(String(64))

    # Configuration pointers (see README "Snapshot and version management").
    draft_revision_id: Mapped[str | None] = mapped_column(String(64))
    draft_base_revision_id: Mapped[str | None] = mapped_column(String(64))
    last_validated_revision_id: Mapped[str | None] = mapped_column(String(64))
    published_revision_id: Mapped[str | None] = mapped_column(String(64))
    last_successful_snapshot_id: Mapped[str | None] = mapped_column(String(64))

    # Single-writer lock: at most one job mutates a project at a time.
    lock_job_id: Mapped[str | None] = mapped_column(String(64))
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ConfigRevision(Base):
    """Immutable version of a project's complete Puck configuration (all pages)."""

    __tablename__ = "config_revisions"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_revision_number"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rev"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    # draft | proposed | validated | published | rejected
    status: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(400))
    parent_revision_id: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(200))
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComponentDefinition(Base):
    """Project-specific extension of the trusted component registry (created by Path B)."""

    __tablename__ = "component_definitions"
    __table_args__ = (Index("ix_component_project_name", "project_id", "name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cmp"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(100))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))  # proposed | active | retired
    job_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_job_idempotency"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("job"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(30), index=True)
    path: Mapped[str | None] = mapped_column(String(30))
    outcome: Mapped[str | None] = mapped_column(String(30))
    request_text: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))

    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    plan_hash: Mapped[str | None] = mapped_column(String(64))
    clarification_answers: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # Base versions captured at analysis time; used for stale-revision detection.
    base_revision_id: Mapped[str | None] = mapped_column(String(64))
    base_commit_sha: Mapped[str | None] = mapped_column(String(64))
    proposed_revision_id: Mapped[str | None] = mapped_column(String(64))
    proposal_key: Mapped[str | None] = mapped_column(String(400))
    diff_key: Mapped[str | None] = mapped_column(String(400))

    branch: Mapped[str | None] = mapped_column(String(200))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    commit_sha: Mapped[str | None] = mapped_column(String(64))  # proposed commit on the job branch
    merged_commit_sha: Mapped[str | None] = mapped_column(String(64))  # commit on the default branch
    deployment_id: Mapped[str | None] = mapped_column(String(64))
    snapshot_id: Mapped[str | None] = mapped_column(String(64))

    requires_approval: Mapped[bool] = mapped_column(default=False)
    approved_plan_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failed_stage: Mapped[str | None] = mapped_column(String(30))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)

    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    """Append-only, structured record of every stage transition and notable step."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(30))
    to_state: Mapped[str | None] = mapped_column(String(30))
    level: Mapped[str] = mapped_column(String(10), default="info")
    code: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobCommit(Base):
    """Every commit the harness created, whether it was eventually deployed or not."""

    __tablename__ = "job_commits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    sha: Mapped[str] = mapped_column(String(64))
    ref: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # branch | merge | restore
    status: Mapped[str] = mapped_column(String(20))  # created | validated | failed | merged | deployed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (UniqueConstraint("job_id", "attempt", name="uq_deployment_attempt"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dep"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str | None] = mapped_column(String(200))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    config_revision_id: Mapped[str | None] = mapped_column(String(64))
    previous_published_revision_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    """Immutable record of a verified, successful deployment."""

    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("snap"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"))
    config_revision_id: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    registry_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_key: Mapped[str] = mapped_column(String(400))
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    build: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rollback_of_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(Snapshot, "before_update")
def _snapshots_are_immutable(_mapper, _conn, _target):  # type: ignore[no-untyped-def]
    raise RuntimeError("Snapshots are immutable once recorded.")


@event.listens_for(ConfigRevision, "before_update")
def _revision_content_is_immutable(_mapper, _conn, target):  # type: ignore[no-untyped-def]
    from sqlalchemy import inspect

    state = inspect(target)
    for attr in ("content_hash", "storage_key", "project_id", "tenant_id", "number"):
        if state.attrs[attr].history.has_changes():
            raise RuntimeError(f"ConfigRevision.{attr} is immutable.")

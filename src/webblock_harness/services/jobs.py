"""Harness job lifecycle: creation, explicit state transitions, events, locking and human actions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webblock_harness.db import utcnow
from webblock_harness.domain import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    WAITING_STATES,
    HarnessError,
    JobKind,
    JobState,
    PuckOp,
)
from webblock_harness.logging_utils import get_logger
from webblock_harness.models import Job, JobEvent, Project, Snapshot
from webblock_harness.puck.normalize import ConfigFormatError, normalize_site
from webblock_harness.security.auth import Principal, Role
from webblock_harness.services.container import Container
from webblock_harness.services.errors import Conflict, NotFound, Unprocessable
from webblock_harness.services.projects import ProjectService, require

log = get_logger(__name__)
_OPS = TypeAdapter(list[PuckOp])
MAX_REQUEST_CHARS = 4000

# Stages from which a retry may resume, and where it resumes.
RESUME_FROM = {
    JobState.ANALYZING: JobState.ANALYZING,
    JobState.APPLYING: JobState.APPLYING,
    JobState.VALIDATING: JobState.VALIDATING,
    JobState.COMMITTING: JobState.COMMITTING,
    JobState.BUILDING: JobState.BUILDING,
    JobState.DEPLOYING: JobState.BUILDING,  # a failed deployment is re-run as a new deployment attempt
    # A failed verification restores the previous version; retrying redeploys rather than re-checking.
    JobState.VERIFYING: JobState.BUILDING,
}
CANCELLABLE = {
    JobState.QUEUED,
    JobState.ANALYZING,
    JobState.NEEDS_CLARIFICATION,
    JobState.PLANNED,
    JobState.AWAITING_APPROVAL,
    JobState.APPLYING,
    JobState.VALIDATING,
}


class InvalidTransition(Exception):
    pass


class JobService:
    def __init__(self, c: Container) -> None:
        self.c = c
        self.projects = ProjectService(c)

    # -- events & transitions -------------------------------------------------
    def event(
        self,
        session: Session,
        job: Job,
        code: str,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> None:
        session.add(
            JobEvent(
                tenant_id=job.tenant_id,
                job_id=job.id,
                code=code,
                message=message,
                level=level,
                data=data or {},
                from_state=from_state,
                to_state=to_state,
            )
        )
        log.info(
            message,
            extra={
                "job_id": job.id,
                "project_id": job.project_id,
                "tenant_id": job.tenant_id,
                "event": code,
                "state": to_state or job.state,
                **({"data": data} if data else {}),
            },
        )

    def transition(
        self, session: Session, job: Job, to: JobState, code: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        current = JobState(job.state)
        if to not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransition(f"Job {job.id}: {current.value} -> {to.value} is not allowed.")
        job.state = to.value
        if to in TERMINAL_STATES:
            job.finished_at = utcnow()
        self.event(session, job, code, message, data=data, from_state=current.value, to_state=to.value)
        if to in TERMINAL_STATES or to in WAITING_STATES:
            self.release_lock(session, job)

    def fail(self, session: Session, job: Job, error: HarnessError) -> None:
        job.error = error.model_dump(mode="json")
        job.failed_stage = error.stage.value
        self.transition(
            session,
            job,
            JobState.FAILED,
            error.code,
            error.message,
            data={"retryable": error.retryable, "hint": error.hint, **error.details},
        )

    # -- locking --------------------------------------------------------------
    def acquire_lock(self, session: Session, job: Job) -> bool:
        now = utcnow()
        result = session.execute(
            update(Project)
            .where(
                Project.id == job.project_id,
                or_(Project.lock_job_id.is_(None), Project.lock_job_id == job.id, Project.lock_expires_at < now),
            )
            .values(lock_job_id=job.id, lock_expires_at=now + timedelta(seconds=self.c.settings.project_lock_ttl_seconds))
        )
        session.flush()
        return result.rowcount == 1  # type: ignore[attr-defined]

    def heartbeat(self, session: Session, job: Job) -> None:
        session.execute(
            update(Project)
            .where(Project.id == job.project_id, Project.lock_job_id == job.id)
            .values(lock_expires_at=utcnow() + timedelta(seconds=self.c.settings.project_lock_ttl_seconds))
        )

    def release_lock(self, session: Session, job: Job) -> None:
        session.execute(
            update(Project)
            .where(Project.id == job.project_id, Project.lock_job_id == job.id)
            .values(lock_job_id=None, lock_expires_at=None)
        )

    # -- queries ----------------------------------------------------------------
    def get(self, session: Session, principal: Principal, job_id: str) -> Job:
        job = session.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == principal.tenant_id))
        if job is None:
            raise NotFound("job_not_found", "Job not found.")
        return job

    def list_jobs(self, session: Session, principal: Principal, project_id: str, limit: int = 50) -> list[Job]:
        self.projects.get(session, principal, project_id)
        return list(
            session.scalars(
                select(Job)
                .where(Job.project_id == project_id, Job.tenant_id == principal.tenant_id)
                .order_by(Job.created_at.desc())
                .limit(limit)
            )
        )

    def events(self, session: Session, principal: Principal, job_id: str) -> list[JobEvent]:
        job = self.get(session, principal, job_id)
        return list(
            session.scalars(
                select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.tenant_id == principal.tenant_id).order_by(JobEvent.id)
            )
        )

    # -- creation ---------------------------------------------------------------
    def create_change_job(
        self,
        session: Session,
        principal: Principal,
        project_id: str,
        *,
        request_text: str | None,
        proposed_config: dict[str, Any] | None,
        operations: list[dict[str, Any]] | None,
        use_draft: bool,
        idempotency_key: str | None,
    ) -> tuple[Job, bool]:
        """Returns (job, created). Re-submitting the same idempotency key returns the original job."""
        require(principal, Role.EDITOR)
        project = self.projects.get(session, principal, project_id)
        if idempotency_key:
            existing = session.scalar(select(Job).where(Job.project_id == project.id, Job.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, False
        sources = sum(x is not None and x is not False for x in (proposed_config, operations, use_draft or None))
        if sources > 1:
            raise Unprocessable("ambiguous_input", "Provide at most one of proposed_config, operations or use_draft.")
        if not (request_text and request_text.strip()) and sources == 0:
            raise Unprocessable("empty_request", "Provide a request, a proposed configuration, operations or use_draft.")
        if request_text and len(request_text) > MAX_REQUEST_CHARS:
            raise Unprocessable("request_too_long", f"Requests are limited to {MAX_REQUEST_CHARS} characters.")
        inputs: dict[str, Any] = {}
        if not project.repo_provisioned:
            raise Conflict("not_deployed", "Deploy the site once before submitting changes; until then, edit it in the editor.")
        if use_draft:
            if project.draft_revision_id in (None, project.published_revision_id):
                raise Unprocessable("no_draft", "There is no unpublished draft.")
            inputs["draft_revision_id"] = project.draft_revision_id
            inputs["draft_base_revision_id"] = project.draft_base_revision_id or project.published_revision_id
        if proposed_config is not None:
            try:
                normalized = normalize_site(proposed_config)
            except ConfigFormatError as exc:
                raise Unprocessable("invalid_config", str(exc)) from exc
            key, _ = self.c.store.put_json(project.tenant_id, project.id, "inputs", normalized)
            inputs["proposed_config_key"] = key
        if operations is not None:
            try:
                inputs["operations"] = [op.model_dump(mode="json") for op in _OPS.validate_python(operations)]
            except ValidationError as exc:
                raise Unprocessable(
                    "invalid_operations", "Operations failed schema validation.", {"errors": exc.errors()[:10]}
                ) from exc
        job = Job(
            tenant_id=project.tenant_id,
            project_id=project.id,
            kind=JobKind.CHANGE.value,
            state=JobState.QUEUED.value,
            request_text=(request_text or "").strip() or None,
            inputs=inputs,
            idempotency_key=idempotency_key,
            created_by=principal.user_id,
        )
        session.add(job)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("duplicate_idempotency_key", "A job with this idempotency key already exists.") from exc
        self.event(
            session,
            job,
            "job_created",
            "Job queued.",
            to_state=JobState.QUEUED.value,
            data={"inputs": sorted(inputs), "has_request_text": bool(job.request_text)},
        )
        return job, True

    def create_deploy_job(
        self, session: Session, principal: Principal, project_id: str, idempotency_key: str | None
    ) -> tuple[Job, bool]:
        """The editor's Deploy button: first deploy provisions the repository; later deploys publish the draft."""
        require(principal, Role.EDITOR)
        project = self.projects.get(session, principal, project_id)
        if idempotency_key:
            existing = session.scalar(select(Job).where(Job.project_id == project.id, Job.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, False
        if project.repo_provisioned:
            if project.draft_revision_id in (None, project.published_revision_id):
                raise Unprocessable("nothing_to_deploy", "The deployed site already matches the editor; make a change first.")
            return self.create_change_job(
                session,
                principal,
                project_id,
                request_text=None,
                proposed_config=None,
                operations=None,
                use_draft=True,
                idempotency_key=idempotency_key,
            )
        running = session.scalar(
            select(Job).where(
                Job.project_id == project.id,
                Job.kind == JobKind.PROVISION.value,
                Job.state.not_in([s.value for s in TERMINAL_STATES]),
            )
        )
        if running is not None:
            return running, False
        job = Job(
            tenant_id=project.tenant_id,
            project_id=project.id,
            kind=JobKind.PROVISION.value,
            state=JobState.QUEUED.value,
            request_text=f"Create {project.repo_owner}/{project.repo_name} and publish the site",
            inputs={"draft_revision_id": project.draft_revision_id, "resume_state": JobState.COMMITTING.value},
            idempotency_key=idempotency_key,
            created_by=principal.user_id,
        )
        session.add(job)
        session.flush()
        self.event(
            session,
            job,
            "job_created",
            "First deploy queued: repository will be created and the site pushed.",
            to_state=JobState.QUEUED.value,
        )
        return job, True

    def create_upgrade_job(
        self, session: Session, principal: Principal, project_id: str, idempotency_key: str | None
    ) -> tuple[Job, bool]:
        """Push the current WebBlock site code (backend, Business Portal, fixes) to an already-deployed site."""
        require(principal, Role.EDITOR)
        project = self.projects.get(session, principal, project_id)
        if idempotency_key:
            existing = session.scalar(select(Job).where(Job.project_id == project.id, Job.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, False
        if not project.repo_provisioned:
            raise Conflict("not_deployed", "Deploy the site first; the first deploy already includes the latest code.")
        if not project.template_id:
            raise Unprocessable("not_a_webblock_site", "Only sites created from a WebBlock template can be updated this way.")
        running = session.scalar(
            select(Job).where(
                Job.project_id == project.id,
                Job.kind == JobKind.UPGRADE.value,
                Job.state.not_in([s.value for s in TERMINAL_STATES]),
            )
        )
        if running is not None:
            return running, False
        job = Job(
            tenant_id=project.tenant_id,
            project_id=project.id,
            kind=JobKind.UPGRADE.value,
            state=JobState.QUEUED.value,
            request_text=f"Update {project.repo_owner}/{project.repo_name} to the latest WebBlock site code",
            inputs={"resume_state": JobState.COMMITTING.value},
            idempotency_key=idempotency_key,
            created_by=principal.user_id,
        )
        session.add(job)
        session.flush()
        self.event(
            session,
            job,
            "job_created",
            "Code update queued: files you edited on GitHub and your page content are left as they are.",
            to_state=JobState.QUEUED.value,
        )
        return job, True

    def create_rollback_job(
        self, session: Session, principal: Principal, project_id: str, snapshot_id: str, idempotency_key: str | None
    ) -> tuple[Job, bool]:
        require(principal, Role.OWNER)
        project = self.projects.get(session, principal, project_id)
        snap = session.scalar(
            select(Snapshot).where(
                Snapshot.id == snapshot_id, Snapshot.project_id == project.id, Snapshot.tenant_id == principal.tenant_id
            )
        )
        if snap is None:
            raise NotFound("snapshot_not_found", "Snapshot not found.")
        if idempotency_key:
            existing = session.scalar(select(Job).where(Job.project_id == project.id, Job.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, False
        job = Job(
            tenant_id=project.tenant_id,
            project_id=project.id,
            kind=JobKind.ROLLBACK.value,
            state=JobState.QUEUED.value,
            request_text=f"Roll back to snapshot {snap.id}",
            inputs={"snapshot_id": snap.id, "resume_state": JobState.COMMITTING.value},
            idempotency_key=idempotency_key,
            created_by=principal.user_id,
        )
        session.add(job)
        session.flush()
        self.event(session, job, "job_created", f"Rollback to snapshot {snap.id} queued.", to_state=JobState.QUEUED.value)
        return job, True

    # -- human actions ------------------------------------------------------------
    def approve(self, session: Session, principal: Principal, job_id: str, plan_hash: str) -> Job:
        require(principal, Role.OWNER)
        job = self.get(session, principal, job_id)
        if job.state != JobState.AWAITING_APPROVAL.value:
            raise Conflict("not_awaiting_approval", f"Job is {job.state}, not awaiting approval.")
        if plan_hash != job.plan_hash:
            raise Conflict(
                "plan_changed",
                "The plan changed since you reviewed it; review the current plan.",
                {"current_plan_hash": job.plan_hash},
            )
        job.approved_plan_hash = plan_hash
        job.approved_by = principal.user_id
        job.approved_at = utcnow()
        job.inputs = {**job.inputs, "resume_state": JobState.APPLYING.value}
        self.transition(
            session,
            job,
            JobState.QUEUED,
            "plan_approved",
            f"Plan approved by {principal.user_id}.",
            data={"plan_hash": plan_hash, "reasons": (job.plan or {}).get("approval_reasons", [])},
        )
        return job

    def clarify(self, session: Session, principal: Principal, job_id: str, answers: list[str]) -> Job:
        require(principal, Role.EDITOR)
        job = self.get(session, principal, job_id)
        if job.state != JobState.NEEDS_CLARIFICATION.value:
            raise Conflict("not_awaiting_clarification", f"Job is {job.state}.")
        cleaned = [a.strip()[:1000] for a in answers if a.strip()]
        if not cleaned:
            raise Unprocessable("empty_answers", "Provide at least one answer.")
        job.clarification_answers = [*job.clarification_answers, *cleaned]
        job.inputs = {**job.inputs, "resume_state": JobState.ANALYZING.value}
        self.transition(session, job, JobState.QUEUED, "clarified", "Clarification received; re-analysing.")
        return job

    def cancel(self, session: Session, principal: Principal, job_id: str) -> Job:
        require(principal, Role.EDITOR)
        job = self.get(session, principal, job_id)
        state = JobState(job.state)
        if state in TERMINAL_STATES:
            raise Conflict("job_finished", f"Job already {job.state}.")
        if state not in CANCELLABLE:
            raise Conflict("not_cancellable", "The change is already being merged or deployed and cannot be cancelled safely.")
        if state in (JobState.ANALYZING, JobState.APPLYING, JobState.VALIDATING, JobState.PLANNED):
            job.cancel_requested = True  # the worker stops at the next stage boundary
            self.event(session, job, "cancel_requested", "Cancellation requested.")
        else:
            self.transition(session, job, JobState.CANCELLED, "cancelled", f"Cancelled by {principal.user_id}.")
        return job

    def retry(self, session: Session, principal: Principal, job_id: str) -> Job:
        require(principal, Role.EDITOR)
        job = self.get(session, principal, job_id)
        if job.state != JobState.FAILED.value:
            raise Conflict("not_failed", f"Only failed jobs can be retried (job is {job.state}).")
        error = HarnessError.model_validate(job.error) if job.error else None
        if error is None or not error.retryable:
            raise Conflict(
                "not_retryable", "This failure is not safe to retry; submit a new request instead.", {"error": job.error}
            )
        resume = RESUME_FROM.get(error.stage)
        if resume is None:
            raise Conflict("not_retryable", f"Cannot resume from {error.stage.value}.")
        job.attempts += 1
        job.inputs = {**job.inputs, "resume_state": resume.value}
        job.error = None
        self.transition(
            session, job, JobState.QUEUED, "retry_requested", f"Retry #{job.attempts} requested; resuming at {resume.value}."
        )
        return job

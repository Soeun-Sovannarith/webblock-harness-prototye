"""The harness pipeline.

    request → normalise → load context → plan → classify → select path → apply →
    validate → commit → build/deploy → verify → record snapshot

Each stage is a method with a defined input (the persisted job + project state)
and output (a state transition plus persisted results). Stages are re-entrant:
external side effects are recorded on the job before moving on, so a crash or a
retry resumes without duplicating commits, PRs or deployments. Long waits (CI,
deployments) are polled one step at a time, so no DB transaction is held open.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from webblock_harness.analyzer.analyzer import AnalysisInput, ChangeAnalyzer, SourceContext
from webblock_harness.analyzer.llm import PlannerError
from webblock_harness.codegen.validator import validate_patch
from webblock_harness.config import ConfigDeliveryMode
from webblock_harness.db import utcnow
from webblock_harness.domain import (
    TERMINAL_STATES,
    WAITING_STATES,
    ApprovalReason,
    ChangePlan,
    CheckResult,
    CheckStatus,
    CodeChangeProposal,
    DeploymentStatus,
    ExecutionPath,
    FileAction,
    HarnessError,
    JobKind,
    JobOutcome,
    JobState,
    PuckOp,
    ValidationIssue,
    ValidationReport,
)
from webblock_harness.integrations.deploy import DeployRequest
from webblock_harness.integrations.git import FileBlob, FileWrite, GitError, StaleRefError
from webblock_harness.logging_utils import get_logger
from webblock_harness.models import (
    ComponentDefinition,
    ConfigRevision,
    Deployment,
    Job,
    JobCommit,
    Project,
    Snapshot,
)
from webblock_harness.puck.diff import diff_sites
from webblock_harness.puck.normalize import ConfigFormatError, config_hash, normalize_site
from webblock_harness.puck.ops import OpConflict, apply_ops
from webblock_harness.puck.tree import index_components
from webblock_harness.puck.validate import validate_site
from webblock_harness.registry.registry import ComponentRegistry
from webblock_harness.security.paths import PathPolicyError, in_generated_area, is_protected, normalize_repo_path
from webblock_harness.services.container import Container
from webblock_harness.services.errors import StageFailure
from webblock_harness.services.jobs import CANCELLABLE, JobService
from webblock_harness.services.projects import ProjectService
from webblock_harness.templates.catalog import TEMPLATES, SiteProfile, seed_data
from webblock_harness.templates.scaffold import config_file_content, render_site, site_code_version

log = get_logger(__name__)
MAX_CONTEXT_BYTES = 400_000
_REPO_CREATE_HINTS = {
    "github_permission_denied": (
        "Give the harness token permission to create repositories: a fine-grained token with resource owner = the "
        "organisation, repository access = All repositories, and Repository permissions → Administration: Read and "
        "write (plus Contents: Read and write). If the organisation requires approval for fine-grained tokens, approve "
        "it in the organisation settings. Then press Retry."
    ),
    "repo_exists": "The name was just taken; retry. WebBlock reuses a repository it created for this job.",
}
MAX_STEPS = 500


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Step(Enum):
    CONTINUE = "continue"  # state changed; run the next stage
    WAIT = "wait"  # waiting on an external system; poll again later
    DEFER = "defer"  # could not start (project busy); re-queue later


class Pipeline:
    def __init__(self, c: Container) -> None:
        self.c = c
        self.jobs = JobService(c)
        self.projects = ProjectService(c)
        self.analyzer = ChangeAnalyzer(c.planner)
        self._handlers = {
            JobState.QUEUED: self._start,
            JobState.ANALYZING: self._analyze,
            JobState.PLANNED: self._planned,
            JobState.APPLYING: self._apply,
            JobState.VALIDATING: self._validate,
            JobState.COMMITTING: self._commit,
            JobState.BUILDING: self._build,
            JobState.DEPLOYING: self._deploy,
            JobState.VERIFYING: self._verify,
        }

    # ------------------------------------------------------------------ driver
    def run(self, job_id: str) -> str:
        """Drive a job until it finishes, waits for a human, or is deferred. Returns the resulting state."""
        for _ in range(MAX_STEPS):
            step, state = self._step(job_id)
            if step is Step.DEFER:
                return "deferred"
            if state in TERMINAL_STATES or state in WAITING_STATES:
                return state.value
            if step is Step.WAIT:
                self.c.sleep(self.c.settings.poll_interval_seconds)
        return "deferred"

    def _step(self, job_id: str) -> tuple[Step, JobState]:
        try:
            with self.c.db.session() as s:
                job = s.get(Job, job_id)
                if job is None:
                    raise RuntimeError(f"Job {job_id} disappeared.")
                state = JobState(job.state)
                if state in TERMINAL_STATES or state in WAITING_STATES:
                    return Step.CONTINUE, state
                if job.cancel_requested and state in CANCELLABLE:
                    self._discard_proposed(s, job)
                    self.jobs.transition(s, job, JobState.CANCELLED, "cancelled", "Cancelled at a stage boundary.")
                    return Step.CONTINUE, JobState.CANCELLED
                if state != JobState.QUEUED:
                    self.jobs.heartbeat(s, job)
                try:
                    step = self._handlers[state](s, job)
                except StageFailure as failure:
                    if not failure.error.retryable:
                        self._discard_proposed(s, job, only_if_unpublished=True)
                    self.jobs.fail(s, job, failure.error)
                    step = Step.CONTINUE
                return step, JobState(job.state)
        except Exception as exc:  # unexpected: record a retryable failure in a fresh transaction
            log.exception("stage_crashed", extra={"job_id": job_id})
            with self.c.db.session() as s:
                job = s.get(Job, job_id)
                assert job is not None
                state = JobState(job.state)
                if state not in TERMINAL_STATES:
                    self.jobs.fail(
                        s,
                        job,
                        HarnessError(
                            code="internal_error",
                            message=f"{exc.__class__.__name__}: {exc}"[:500],
                            stage=state,
                            retryable=state not in (JobState.QUEUED,),
                            hint="Unexpected error; the job can be retried. See server logs for the stack trace.",
                        ),
                    )
                return Step.CONTINUE, JobState(job.state)

    # ------------------------------------------------------------------ helpers
    def _project(self, s: Session, job: Job) -> Project:
        project = s.get(Project, job.project_id)
        assert project is not None and project.tenant_id == job.tenant_id
        return project

    def _plan(self, job: Job) -> ChangePlan:
        return ChangePlan.model_validate(job.plan)

    def _delivery(self) -> ConfigDeliveryMode:
        return self.c.settings.config_delivery_mode

    def _git(self, stage: JobState, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except StaleRefError as exc:
            raise StageFailure(
                "stale_ref",
                exc.message,
                stage,
                retryable=True,
                hint="The branch moved while the harness was updating it; retry to reconcile.",
            ) from exc
        except GitError as exc:
            raise StageFailure(exc.code, exc.message, stage, retryable=exc.retryable) from exc

    def _deadline_passed(self, job: Job, stage: JobState, timeout: float) -> bool:
        deadlines = dict(job.inputs.get("deadlines", {}))
        key = f"{stage.value}:{job.attempts}"
        if key not in deadlines:
            deadlines[key] = (utcnow() + timedelta(seconds=timeout)).isoformat()
            job.inputs = {**job.inputs, "deadlines": deadlines}
            return False
        return utcnow() > datetime.fromisoformat(deadlines[key])

    def _registry(self, s: Session, project: Project, job: Job) -> ComponentRegistry:
        return self.projects.registry_for(s, project, include_proposed_for_job=job.id)

    def _discard_proposed(self, s: Session, job: Job, only_if_unpublished: bool = False) -> None:
        if job.proposed_revision_id and job.kind == JobKind.CHANGE.value:
            rev = s.get(ConfigRevision, job.proposed_revision_id)
            if rev and rev.status in ("proposed", "validated"):
                project = self._project(s, job)
                if not only_if_unpublished or project.published_revision_id != rev.id:
                    rev.status = "rejected"

    def _preview_url(self, project: Project, revision_id: str) -> str | None:
        t = self.c.settings.preview_url_template
        return t.format(site_key=project.site_key, revision_id=revision_id, repo_name=project.repo_name) if t else None

    # ------------------------------------------------------------------ stages
    def _start(self, s: Session, job: Job) -> Step:
        if not self.jobs.acquire_lock(s, job):
            project = self._project(s, job)
            if not job.inputs.get("waiting_for_lock"):
                job.inputs = {**job.inputs, "waiting_for_lock": True}
                self.jobs.event(s, job, "waiting_for_lock", f"Project is busy with job {project.lock_job_id}; queued behind it.")
            return Step.DEFER
        inputs = dict(job.inputs)
        resume = inputs.pop("resume_state", None)
        inputs.pop("waiting_for_lock", None)
        job.inputs = inputs
        target = (
            JobState(resume) if resume else (JobState.COMMITTING if job.kind == JobKind.ROLLBACK.value else JobState.ANALYZING)
        )
        self.jobs.transition(s, job, target, "stage_started", f"Lock acquired; starting {target.value}.")
        return Step.CONTINUE

    # -- 1-5: normalise, load context, plan, classify, select path ------------
    def _analyze(self, s: Session, job: Job) -> Step:
        project = self._project(s, job)
        registry = self._registry(s, project, job)
        base_rev, base = self.projects.published(s, project)
        repo = self.projects.repo(project)
        head = self._git(JobState.ANALYZING, self.c.git.get_branch_head, repo, project.default_branch)
        files = self._git(JobState.ANALYZING, self.c.git.list_files, repo, head) if head else []
        source = SourceContext(
            head_sha=head,
            files=files,
            history=(lambda path: self.c.git.file_history(repo, path, head, 5)) if head else None,
            harness_author_email=self.c.settings.harness_commit_author_email,
        )
        proposed = None
        explicit = None
        if "proposed_config_key" in job.inputs:
            proposed = self.c.store.get_json(job.tenant_id, job.inputs["proposed_config_key"])
        elif "draft_revision_id" in job.inputs:
            draft = self.projects.load(project, self.projects.revision(s, project, job.inputs["draft_revision_id"]))
            draft_base_id = job.inputs.get("draft_base_revision_id") or base_rev.id
            # Three-way: replay only what the client changed in the editor (draft base → draft) onto the
            # latest published version, so changes made elsewhere since (e.g. on GitHub) are preserved.
            draft_base = (
                base
                if draft_base_id == base_rev.id
                else self.projects.load(project, self.projects.revision(s, project, draft_base_id))
            )
            explicit = diff_sites(draft_base, draft, registry).ops
        if "operations" in job.inputs:
            from pydantic import TypeAdapter

            explicit = TypeAdapter(list[PuckOp]).validate_python(job.inputs["operations"])
        snap = s.get(Snapshot, project.last_successful_snapshot_id) if project.last_successful_snapshot_id else None
        base_label = (
            f"last deployed snapshot {snap.id}"
            if snap
            else f"published baseline revision #{base_rev.number} (no harness deployment yet)"
        )
        try:
            plan = self.analyzer.analyze(
                AnalysisInput(
                    base_config=base,
                    registry=registry,
                    request_text=job.request_text or "",
                    proposed_config=proposed,
                    explicit_ops=explicit,
                    answers=list(job.clarification_answers),
                    source=source,
                    base_label=base_label,
                    auto_approve=self.c.auto_approve,
                    required_checks=self.c.settings.required_checks_for,
                )
            )
        except PlannerError as exc:
            raise StageFailure(exc.code, exc.message, JobState.ANALYZING, retryable=exc.retryable) from exc

        job.plan = plan.model_dump(mode="json")
        job.plan_hash = plan.plan_hash
        job.path = plan.path.value
        job.requires_approval = plan.requires_approval
        job.base_revision_id = base_rev.id
        job.base_commit_sha = head
        self.jobs.event(
            s,
            job,
            "plan_created",
            plan.summary,
            data={
                "path": plan.path.value,
                "plan_hash": plan.plan_hash,
                "decided_by": plan.decided_by,
                "changes": len(plan.changes),
                "approval_reasons": [r.value for r in plan.approval_reasons],
                "security_flags": plan.security_flags,
                "llm_overridden": plan.llm_overridden,
            },
        )

        if plan.path == ExecutionPath.NOOP:
            job.outcome = JobOutcome.NO_CHANGES.value
            self.jobs.transition(
                s,
                job,
                JobState.SUCCEEDED,
                "no_changes",
                "Requested state already matches the deployed state; nothing generated, committed or deployed.",
            )
        elif plan.path == ExecutionPath.CLARIFICATION:
            self.jobs.transition(
                s,
                job,
                JobState.NEEDS_CLARIFICATION,
                "clarification_needed",
                "Waiting for the client to clarify the request.",
                data={"questions": [q.question for q in plan.clarifications]},
            )
        elif plan.path == ExecutionPath.REJECTED:
            raise StageFailure(
                "plan_rejected",
                "; ".join(plan.rationale[-3:])[:500],
                JobState.ANALYZING,
                details={"rationale": plan.rationale, "security_flags": plan.security_flags},
                hint="Adjust the request or configuration; this request cannot be applied as given.",
            )
        else:
            self.jobs.transition(
                s,
                job,
                JobState.PLANNED,
                "path_selected",
                f"Execution path: {plan.path.value}.",
                data={"rationale": plan.rationale},
            )
        return Step.CONTINUE

    def _planned(self, s: Session, job: Job) -> Step:
        plan = self._plan(job)
        if plan.requires_approval and job.approved_plan_hash != job.plan_hash:
            self.jobs.transition(
                s,
                job,
                JobState.AWAITING_APPROVAL,
                "approval_required",
                "This change needs explicit approval before anything is applied.",
                data={"reasons": [r.value for r in plan.approval_reasons], "plan_hash": job.plan_hash},
            )
        else:
            self.jobs.transition(s, job, JobState.APPLYING, "applying", "Applying the planned change.")
        return Step.CONTINUE

    # -- 6: apply ---------------------------------------------------------------
    def _apply(self, s: Session, job: Job) -> Step:
        plan = self._plan(job)
        if plan.path == ExecutionPath.RUNTIME_CONFIG:
            return self._apply_runtime(s, job, plan)
        return self._apply_source(s, job, plan)

    def _apply_runtime(self, s: Session, job: Job, plan: ChangePlan) -> Step:
        project = self._project(s, job)
        registry = self._registry(s, project, job)
        cur_rev, current = self.projects.published(s, project)
        if job.proposed_revision_id:
            existing = s.get(ConfigRevision, job.proposed_revision_id)
            if existing and existing.parent_revision_id == cur_rev.id and existing.status in ("proposed", "validated"):
                self.jobs.transition(
                    s, job, JobState.VALIDATING, "reused_revision", "Reusing the proposed revision from a previous attempt."
                )
                return Step.CONTINUE
        if cur_rev.id != job.base_revision_id:
            self.jobs.event(
                s,
                job,
                "base_moved",
                "The published configuration changed since planning; re-applying the targeted patch with its preconditions.",
                data={"planned_base": job.base_revision_id, "current": cur_rev.id},
            )
        try:
            candidate = apply_ops(current, plan.operations, registry)
        except OpConflict as exc:
            raise StageFailure(
                "config_conflict",
                exc.message,
                JobState.APPLYING,
                details={"op_index": exc.op_index, "conflict": exc.code},
                hint="The page changed since the request was analysed; submit the request again.",
            ) from exc
        if config_hash(candidate) == cur_rev.content_hash:
            job.outcome = JobOutcome.NO_CHANGES.value
            self.jobs.transition(
                s, job, JobState.SUCCEEDED, "no_changes", "The patch produces the already-published configuration."
            )
            return Step.CONTINUE
        rev = self.projects.create_revision(
            s, project, candidate, status="proposed", created_by=job.created_by, job_id=job.id, parent_id=cur_rev.id
        )
        job.proposed_revision_id = rev.id
        self.jobs.transition(
            s,
            job,
            JobState.VALIDATING,
            "config_patched",
            f"Applied {len(plan.operations)} targeted operation(s) as revision #{rev.number} (draft, not yet published).",
            data={"revision_id": rev.id, "preview_url": self._preview_url(project, rev.id)},
        )
        return Step.CONTINUE

    def _read_context_files(self, project: Project, head: str, paths: set[str]) -> dict[str, str]:
        repo = self.projects.repo(project)
        out: dict[str, str] = {}
        total = 0
        for raw in sorted(paths):
            try:
                path = normalize_repo_path(raw)
            except PathPolicyError:
                continue
            if is_protected(path):
                continue  # never send secrets or protected infrastructure to the model
            blob = self._git(JobState.APPLYING, self.c.git.read_file, repo, path, head)
            if blob is None or total + len(blob.content) > MAX_CONTEXT_BYTES:
                continue
            total += len(blob.content)
            out[path] = blob.content
        return out

    def _apply_source(self, s: Session, job: Job, plan: ChangePlan) -> Step:
        project = self._project(s, job)
        repo = self.projects.repo(project)
        base_registry = self.projects.registry_for(s, project)
        if job.commit_sha:  # already committed to the job branch on a previous attempt
            self.jobs.transition(
                s, job, JobState.VALIDATING, "reused_commit", f"Reusing job-branch commit {job.commit_sha[:12]}."
            )
            return Step.CONTINUE
        if not self.c.planner.available:
            raise StageFailure(
                "llm_unavailable",
                "Source-code changes need an LLM provider, and none is configured.",
                JobState.APPLYING,
                retryable=True,
                hint="Configure LLM_PROVIDER (ollama or anthropic), then retry.",
            )
        sp = plan.source_plan
        if sp is None:
            raise StageFailure(
                "plan_incomplete",
                "The plan has no implementation plan (it was made without an LLM).",
                JobState.ANALYZING,
                retryable=True,
                hint="Retry to re-analyse with the LLM configured.",
            )

        head = self._git(JobState.APPLYING, self.c.git.get_branch_head, repo, project.default_branch)
        relevant = {normalize_or_raw(f.path) for f in sp.files_to_change} | {normalize_or_raw(p) for p in sp.files_to_read}
        if head != job.base_commit_sha and job.base_commit_sha:
            changed = set(self._git(JobState.APPLYING, self.c.git.changed_files, repo, job.base_commit_sha, head))
            overlap = sorted(changed & relevant)
            if overlap:
                raise StageFailure(
                    "source_conflict",
                    "Files in the plan were changed manually after planning.",
                    JobState.ANALYZING,
                    retryable=True,
                    details={"files": overlap},
                    hint="Retry to re-plan against the latest code; manual edits are never overwritten.",
                )
            self.jobs.event(
                s,
                job,
                "base_moved",
                "Default branch moved without touching planned files; generating against the new head.",
                data={"from": job.base_commit_sha, "to": head},
            )
            job.base_commit_sha = head

        # Generate (or reuse) the proposal --------------------------------------
        if job.proposal_key:
            proposal = CodeChangeProposal.model_validate(self.c.store.get_json(job.tenant_id, job.proposal_key))
        else:
            context = self._read_context_files(project, head, relevant)
            try:
                proposal = self.c.planner.generate_patch(job.request_text or plan.summary, sp, context, base_registry.summary())
            except PlannerError as exc:
                raise StageFailure(exc.code, exc.message, JobState.APPLYING, retryable=exc.retryable) from exc
            key, _ = self.c.store.put_json(job.tenant_id, job.project_id, "proposals", proposal.model_dump(mode="json"))
            job.proposal_key = key
            self.jobs.event(
                s,
                job,
                "patch_generated",
                proposal.summary,
                data={
                    "files": [f.path for f in proposal.files],
                    "new_components": [c.spec.name for c in proposal.new_components],
                },
            )

        # Base content for every touched path (never trust the model's claim about base SHAs)
        base_files: dict[str, FileBlob | None] = {}
        for f in proposal.files:
            p = normalize_or_raw(f.path)
            try:
                normalize_repo_path(p)
            except PathPolicyError:
                continue
            base_files[p] = self._git(JobState.APPLYING, self.c.git.read_file, repo, p, head)
            if f.action != FileAction.CREATE and base_files[p] is not None:
                f.base_sha = base_files[p].sha  # type: ignore[union-attr]

        approved: set[ApprovalReason] = set(self.c.auto_approve)
        if job.approved_plan_hash and job.approved_plan_hash == job.plan_hash:
            approved |= set(plan.approval_reasons)
        source_ctx = SourceContext(
            head_sha=head,
            files=list(base_files),
            history=lambda path: self.c.git.file_history(repo, path, head, 5),
            harness_author_email=self.c.settings.harness_commit_author_email,
        )
        user_authored = source_ctx.user_authored([p for p, b in base_files.items() if b is not None and not in_generated_area(p)])
        review = validate_patch(proposal, sp, base_files, base_registry, self.c.settings, approved, user_authored)

        # Page operations (e.g. placing the new component) validated against the extended registry
        ext_registry = base_registry.with_extensions([nc.spec for nc in proposal.new_components])
        ops = [*plan.operations, *proposal.page_operations]
        candidate = None
        cur_rev, current = self.projects.published(s, project)
        if ops:
            try:
                candidate = apply_ops(current, ops, ext_registry)
                review.report.add(
                    CheckResult(
                        name="page_operations",
                        status=validate_site(candidate, ext_registry).status,
                        issues=validate_site(candidate, ext_registry).issues,
                    )
                )
            except OpConflict as exc:
                review.report.add(
                    CheckResult(
                        name="page_operations",
                        status=CheckStatus.FAILED,
                        issues=[ValidationIssue(code=exc.code, message=exc.message)],
                    )
                )
        diff_key, _ = self.c.store.put_text(job.tenant_id, job.project_id, "diffs", review.diff, ext="diff")
        job.diff_key = diff_key
        job.validation = review.report.model_dump(mode="json")
        if not review.report.passed:
            raise StageFailure(
                "patch_validation_failed",
                "The generated patch failed validation; nothing was committed.",
                JobState.APPLYING,
                details={"issues": [i.model_dump(mode="json") for i in review.report.issues][:50], "stats": review.stats},
                hint="Review the issues; a new request (or retry after approval, where approval is the issue) will regenerate.",
            )

        # Record proposed registry extensions (activated only after a verified deployment)
        for nc in proposal.new_components:
            s.add(
                ComponentDefinition(
                    tenant_id=project.tenant_id,
                    project_id=project.id,
                    name=nc.spec.name,
                    spec=nc.spec.model_dump(mode="json"),
                    status="proposed",
                    job_id=job.id,
                )
            )
        writes = [
            FileWrite(normalize_repo_path(f.path), None if f.action == FileAction.DELETE else f.content) for f in proposal.files
        ]
        if candidate is not None:
            rev = self.projects.create_revision(
                s,
                project,
                candidate,
                status="validated",
                created_by=job.created_by,
                job_id=job.id,
                parent_id=cur_rev.id,
                validation=review.report,
            )
            job.proposed_revision_id = rev.id
            if self._delivery() == ConfigDeliveryMode.COMMIT:
                writes.append(FileWrite(self.c.settings.config_repo_path, config_file_content(candidate)))

        branch = f"{self.c.settings.harness_branch_prefix}{job.id}"
        message = f"{proposal.summary[:72]}\n\n{plan.summary[:2000]}\n\nWebBlock-Job: {job.id}\nWebBlock-Plan: {job.plan_hash}"
        sha = self._git(
            JobState.APPLYING,
            self.c.git.commit_files,
            repo,
            branch,
            head,
            writes,
            message,
            create_branch=True,
            reset_job_branch=True,
        )
        s.add(JobCommit(tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=branch, kind="branch", status="created"))
        body = "\n".join(
            [
                plan.summary,
                "",
                "Rationale:",
                *[f"- {r}" for r in plan.rationale],
                "",
                f"Diff stats: {json.dumps(review.stats)}",
                f"WebBlock job: {job.id}",
            ]
        )
        pr = self._git(
            JobState.APPLYING,
            self.c.git.open_pull_request,
            repo,
            branch,
            project.default_branch,
            f"[WebBlock] {proposal.summary[:80]}",
            body,
        )
        job.commit_sha, job.branch, job.pr_number = sha, branch, pr
        self.jobs.transition(
            s,
            job,
            JobState.VALIDATING,
            "patch_committed_to_branch",
            f"Validated patch committed to isolated branch {branch} (PR #{pr}); waiting for CI.",
            data={"commit_sha": sha, **review.stats},
        )
        return Step.CONTINUE

    # -- 7: validate ------------------------------------------------------------
    def _validate(self, s: Session, job: Job) -> Step:
        plan = self._plan(job)
        project = self._project(s, job)
        if plan.path == ExecutionPath.RUNTIME_CONFIG:
            rev = self.projects.revision(s, project, job.proposed_revision_id)
            config = self.projects.load(project, rev)
            registry = self._registry(s, project, job)
            report = ValidationReport(checks=[validate_site(config, registry)])
            # Registry conformance: every component type used must be implemented in the client repo.
            try:
                files = set(
                    self.c.git.list_files(self.projects.repo(project), project.source_commit_sha or project.default_branch)
                )
            except GitError:
                files = set()
            missing: list[ValidationIssue] = []
            if files:
                types = {
                    loc.type
                    for page_key, page in config["pages"].items()
                    for loc in index_components(page_key, page, registry).values()
                }
                for t in sorted(types):
                    spec = registry.get(t)
                    if spec and spec.source_path and spec.source_path not in files:
                        missing.append(
                            ValidationIssue(
                                code="component_not_implemented",
                                message=f"{spec.source_path} is missing from the repository.",
                                location=t,
                            )
                        )
            report.add(
                CheckResult(
                    name="registry_conformance",
                    status=CheckStatus.FAILED if missing else (CheckStatus.PASSED if files else CheckStatus.SKIPPED),
                    issues=missing,
                    details={} if files else {"reason": "repository file list unavailable"},
                )
            )
            rev.validation = report.model_dump(mode="json")
            job.validation = rev.validation
            if not report.passed:
                rev.status = "rejected"
                raise StageFailure(
                    "config_validation_failed",
                    "The patched configuration failed validation; nothing was published.",
                    JobState.VALIDATING,
                    details={"issues": [i.model_dump(mode="json") for i in report.issues][:50]},
                )
            rev.status = "validated"
            project.last_validated_revision_id = rev.id
            nxt = JobState.DEPLOYING if self._delivery() == ConfigDeliveryMode.RUNTIME else JobState.COMMITTING
            self.jobs.transition(s, job, nxt, "config_validated", f"Revision #{rev.number} validated.")
            return Step.CONTINUE

        # Path B: required CI checks on the job-branch commit (isolated runner, no WebBlock credentials)
        required = self.c.settings.required_checks_for({sc.value for sc in plan.scopes})
        runs = {
            r.name: r for r in self._git(JobState.VALIDATING, self.c.git.check_runs, self.projects.repo(project), job.commit_sha)
        }
        failed = [n for n in required if n in runs and runs[n].status == "completed" and runs[n].conclusion != "success"]
        pending = [n for n in required if n not in runs or runs[n].status != "completed"]
        ci_results = [
            CheckResult(
                name=f"ci:{n}",
                status=(
                    CheckStatus.PASSED
                    if n in runs and runs[n].conclusion == "success"
                    else CheckStatus.FAILED
                    if n in failed
                    else CheckStatus.PENDING
                ),
                details={"url": runs[n].url, "summary": runs[n].summary} if n in runs else {},
            )
            for n in required
        ]
        report = ValidationReport.model_validate(job.validation or {"checks": []})
        report.checks = [c for c in report.checks if not c.name.startswith("ci:")] + ci_results
        job.validation = report.model_dump(mode="json")
        if failed:
            self._mark_commit(s, job, job.commit_sha, "failed")
            raise StageFailure(
                "ci_failed",
                f"Required checks failed: {', '.join(failed)}. Nothing was merged or deployed.",
                JobState.VALIDATING,
                details={
                    "checks": {
                        n: {"conclusion": runs[n].conclusion, "url": runs[n].url, "summary": runs[n].summary} for n in failed
                    }
                },
                hint="Inspect the CI logs; submit a corrected request. The live site is unchanged.",
            )
        if pending:
            if self._deadline_passed(job, JobState.VALIDATING, self.c.settings.ci_timeout_seconds):
                raise StageFailure(
                    "ci_timeout",
                    f"Required checks did not complete: {', '.join(pending)}.",
                    JobState.VALIDATING,
                    retryable=True,
                    details={"pending": pending},
                    hint="Make sure the repository's CI reports these check names, then retry.",
                )
            return Step.WAIT
        self._mark_commit(s, job, job.commit_sha, "validated")
        self.jobs.transition(s, job, JobState.COMMITTING, "ci_passed", f"All required checks passed: {', '.join(required)}.")
        return Step.CONTINUE

    def _mark_commit(self, s: Session, job: Job, sha: str | None, status: str) -> None:
        for jc in s.scalars(select(JobCommit).where(JobCommit.job_id == job.id, JobCommit.sha == sha)):
            jc.status = status

    # -- 8: commit --------------------------------------------------------------
    def _commit(self, s: Session, job: Job) -> Step:
        project = self._project(s, job)
        repo = self.projects.repo(project)
        if job.kind == JobKind.ROLLBACK.value:
            return self._commit_rollback(s, job, project)
        if job.kind == JobKind.PROVISION.value:
            return self._commit_provision(s, job, project)
        if job.kind == JobKind.UPGRADE.value:
            return self._commit_upgrade(s, job, project)
        if job.merged_commit_sha:
            self.jobs.transition(s, job, JobState.BUILDING, "reused_merge", f"Already merged as {job.merged_commit_sha[:12]}.")
            return Step.CONTINUE
        plan = self._plan(job)
        head = self._git(JobState.COMMITTING, self.c.git.get_branch_head, repo, project.default_branch)

        if plan.path == ExecutionPath.RUNTIME_CONFIG:  # commit-delivery mode: config file only
            return self._commit_config(s, job, project, plan, head)

        assert job.proposal_key and job.commit_sha and job.branch and job.pr_number and job.base_commit_sha
        proposal = CodeChangeProposal.model_validate(self.c.store.get_json(job.tenant_id, job.proposal_key))
        patch_paths = {normalize_or_raw(f.path) for f in proposal.files}
        if head != job.base_commit_sha:
            changed = set(self._git(JobState.COMMITTING, self.c.git.changed_files, repo, job.base_commit_sha, head))
            overlap = sorted(changed & (patch_paths | {self.c.settings.config_repo_path}))
            if overlap:
                self._mark_commit(s, job, job.commit_sha, "failed")
                raise StageFailure(
                    "source_conflict",
                    "The default branch changed the same files as the generated patch.",
                    JobState.COMMITTING,
                    details={"files": overlap, "base": job.base_commit_sha, "head": head},
                    hint="Manual edits are never overwritten. Submit the request again to regenerate against the latest code.",
                )
            # Reconcile: replay the (disjoint) patch onto the new head, then re-run CI on the rebased commit.
            writes = [
                FileWrite(normalize_repo_path(f.path), None if f.action == FileAction.DELETE else f.content)
                for f in proposal.files
            ]
            if self._delivery() == ConfigDeliveryMode.COMMIT and job.proposed_revision_id:
                rev = self.projects.revision(s, project, job.proposed_revision_id)
                writes.append(FileWrite(self.c.settings.config_repo_path, config_file_content(self.projects.load(project, rev))))
            message = f"{proposal.summary[:72]}\n\nRebased onto {head[:12]}.\n\nWebBlock-Job: {job.id}"
            sha = self._git(
                JobState.COMMITTING,
                self.c.git.commit_files,
                repo,
                job.branch,
                head,
                writes,
                message,
                create_branch=False,
                reset_job_branch=True,
            )
            self._mark_commit(s, job, job.commit_sha, "superseded")
            s.add(
                JobCommit(tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=job.branch or "", kind="branch", status="created")
            )
            job.commit_sha, job.base_commit_sha = sha, head
            self.jobs.transition(
                s,
                job,
                JobState.VALIDATING,
                "rebased",
                "Default branch moved (no overlapping files); patch rebased and CI re-run before merging.",
                data={"commit_sha": sha, "new_base": head},
            )
            return Step.CONTINUE

        title = f"[WebBlock] {proposal.summary[:80]} (job {job.id})"
        sha = self._git(JobState.COMMITTING, self.c.git.merge_pull_request, repo, job.pr_number, job.commit_sha, title)
        s.add(
            JobCommit(tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=project.default_branch, kind="merge", status="merged")
        )
        job.merged_commit_sha = sha
        project.source_commit_sha = sha
        self.jobs.transition(s, job, JobState.BUILDING, "merged", f"PR #{job.pr_number} squash-merged as {sha[:12]}.")
        return Step.CONTINUE

    def _commit_config(self, s: Session, job: Job, project: Project, plan: ChangePlan, head: str | None) -> Step:
        """Commit the site configuration file, changing only what this job changed.

        If the file in the repository differs from what the harness last published (someone edited it on
        GitHub), the job's targeted operations are replayed onto the repository's version instead, so those
        edits survive. If both sides changed the same value, the job stops with a conflict.
        """
        repo = self.projects.repo(project)
        path = self.c.settings.config_repo_path
        rev = self.projects.revision(s, project, job.proposed_revision_id)
        config = self.projects.load(project, rev)
        blob = self._git(JobState.COMMITTING, self.c.git.read_file, repo, path, head) if head else None
        pub_rev, _ = self.projects.published(s, project)
        if blob is not None:
            try:
                repo_config = normalize_site(blob.content)
            except ConfigFormatError as exc:
                raise StageFailure(
                    "repo_config_invalid",
                    f"{path} in the repository is not valid site JSON: {exc}",
                    JobState.COMMITTING,
                    hint=f"Fix {path} on GitHub (or revert the manual edit), then retry.",
                    retryable=True,
                ) from exc
            if config_hash(repo_config) != pub_rev.content_hash:
                registry = self._registry(s, project, job)
                try:
                    reconciled = apply_ops(repo_config, plan.operations, registry)
                except OpConflict as exc:
                    raise StageFailure(
                        "repo_conflict",
                        f"The same content was changed on GitHub and in the editor: {exc.message}",
                        JobState.COMMITTING,
                        details={"path": path, "commit_sha": head, "conflict": exc.code},
                        hint="Open the editor again (it loads the GitHub version), re-apply your change and deploy.",
                    ) from exc
                check = validate_site(reconciled, registry)
                if check.status != CheckStatus.PASSED:
                    raise StageFailure(
                        "config_validation_failed",
                        "Merging with the repository version produced invalid content.",
                        JobState.COMMITTING,
                        details={"issues": [i.model_dump(mode="json") for i in check.issues][:50]},
                    )
                rev.status = "superseded"
                rev = self.projects.create_revision(
                    s,
                    project,
                    reconciled,
                    status="validated",
                    created_by=job.created_by,
                    job_id=job.id,
                    parent_id=rev.id,
                    validation=ValidationReport(checks=[check]),
                )
                job.proposed_revision_id = rev.id
                config = reconciled
                self.jobs.event(
                    s,
                    job,
                    "reconciled_with_repository",
                    f"{path} was edited on GitHub; applied only this job's changes on top of it.",
                    data={"repo_commit": head, "revision_id": rev.id},
                )
        content = config_file_content(config)
        if blob is not None and blob.content == content and head:
            sha = head  # already committed (e.g. retry after a crash) – nothing to push
        else:
            summary = plan.summary if len(plan.summary) <= 200 else plan.summary[:197] + "..."
            message = f"Update site content\n\n{summary}\n\nWebBlock-Job: {job.id}\nWebBlock-Revision: {rev.id}"
            sha = self._git(
                JobState.COMMITTING,
                self.c.git.commit_files,
                repo,
                project.default_branch,
                head,
                [FileWrite(path, content)],
                message,
                create_branch=False,
            )
        s.add(
            JobCommit(tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=project.default_branch, kind="merge", status="merged")
        )
        job.merged_commit_sha = sha
        project.source_commit_sha = sha
        self.jobs.transition(
            s,
            job,
            JobState.BUILDING,
            "config_committed",
            f"Site content committed to {project.default_branch} as {sha[:12]}.",
            data={"path": path, "commit_sha": sha},
        )
        return Step.CONTINUE

    def _commit_provision(self, s: Session, job: Job, project: Project) -> Step:
        """First deploy: create the repository in the organisation and push the complete site."""
        if job.merged_commit_sha:
            self.jobs.transition(
                s, job, JobState.BUILDING, "reused_push", f"Site already pushed as {job.merged_commit_sha[:12]}."
            )
            return Step.CONTINUE
        repo = self.projects.repo(project)
        rev = self.projects.revision(s, project, job.inputs.get("draft_revision_id") or project.draft_revision_id)
        config = self.projects.load(project, rev)
        registry = self._registry(s, project, job)
        check = validate_site(config, registry)
        if check.status != CheckStatus.PASSED:
            raise StageFailure(
                "config_validation_failed",
                "The site content is invalid; nothing was pushed.",
                JobState.COMMITTING,
                details={"issues": [i.model_dump(mode="json") for i in check.issues][:50]},
                hint="Fix the highlighted fields in the editor and deploy again.",
            )
        job.validation = ValidationReport(checks=[check]).model_dump(mode="json")
        info = self._git(JobState.COMMITTING, self.c.git.get_repo, repo)
        if info is None:
            try:
                info = self.c.git.create_repository(
                    repo, f"{project.name} — website built with WebBlock", private=self.c.settings.new_repo_private
                )
            except GitError as exc:
                raise StageFailure(
                    exc.code,
                    exc.message,
                    JobState.COMMITTING,
                    retryable=exc.retryable or exc.code == "repo_exists",
                    hint=_REPO_CREATE_HINTS.get(exc.code, "Check the GitHub connection, then retry."),
                ) from exc
            self.jobs.event(
                s,
                job,
                "repository_created",
                f"Created {repo.full_name} in the organisation.",
                data={"private": self.c.settings.new_repo_private},
            )
        head = self._git(JobState.COMMITTING, self.c.git.get_branch_head, repo, info.default_branch)
        if head is None:
            raise StageFailure(
                "repo_initializing",
                "The new repository has no initial commit yet.",
                JobState.COMMITTING,
                retryable=True,
                hint="GitHub is still initialising the repository; retry in a few seconds.",
            )
        files = self._git(JobState.COMMITTING, self.c.git.list_files, repo, head)
        if f"WebBlock-Job: {job.id}" in self._git(JobState.COMMITTING, self.c.git.commit_message, repo, head):
            sha = head  # pushed by a previous attempt of this job
        elif set(files) <= {"README.md", ".gitignore", "LICENSE"}:
            scaffold = self._site_files(project, repo.name, config)
            message = (
                f"Create {project.name} website with WebBlock\n\nTemplate: {project.template_id or 'custom'}\n"
                f"WebBlock-Job: {job.id}\nWebBlock-Revision: {rev.id}"
            )
            sha = self._git(
                JobState.COMMITTING,
                self.c.git.commit_files,
                repo,
                info.default_branch,
                head,
                [FileWrite(p, c) for p, c in sorted(scaffold.items())],
                message,
                create_branch=False,
            )
        else:
            raise StageFailure(
                "repository_not_empty",
                f"{repo.full_name} already contains files WebBlock did not create.",
                JobState.COMMITTING,
                details={"files": files[:20]},
                hint="Pick a different site name or ask an admin to register the existing repository.",
            )
        s.add(JobCommit(tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=info.default_branch, kind="merge", status="merged"))
        project.default_branch = info.default_branch
        project.repo_provisioned = True
        project.site_code_version = site_code_version()
        project.site_files_manifest = self._manifest(self._site_files(project, repo.name, config))
        project.source_commit_sha = sha
        job.base_revision_id = project.published_revision_id
        job.proposed_revision_id = rev.id
        job.merged_commit_sha = sha
        rev.status = "validated"
        self.jobs.transition(
            s,
            job,
            JobState.BUILDING,
            "site_pushed",
            f"Pushed the site (code + content) to {repo.full_name}@{info.default_branch} as {sha[:12]}.",
            data={"commit_sha": sha, "repository": repo.full_name},
        )
        return Step.CONTINUE

    def _commit_upgrade(self, s: Session, job: Job, project: Project) -> Step:
        """Bring an existing site up to the current WebBlock site code without overwriting anyone's edits.

        A file is written when it is missing, or when it still matches what WebBlock last pushed (nobody edited
        it). Files changed on GitHub since then are kept as they are and listed in the job events. The page
        content (the Puck config) is never touched here.
        """
        if job.merged_commit_sha:
            self.jobs.transition(
                s, job, JobState.BUILDING, "reused_push", f"Code already pushed as {job.merged_commit_sha[:12]}."
            )
            return Step.CONTINUE
        repo = self.projects.repo(project)
        job.proposed_revision_id = project.published_revision_id
        head = self._git(JobState.COMMITTING, self.c.git.get_branch_head, repo, project.default_branch)
        if head is None:
            raise StageFailure("branch_missing", f"{repo.full_name} has no {project.default_branch} branch.", JobState.COMMITTING)
        if f"WebBlock-Job: {job.id}" in self._git(JobState.COMMITTING, self.c.git.commit_message, repo, head):
            sha = head  # pushed by a previous attempt of this job
        else:
            config_path = self.c.settings.config_repo_path
            published = self.projects.load(project, self.projects.revision(s, project, project.published_revision_id))
            target = {p: c for p, c in self._site_files(project, repo.name, published).items() if p != config_path}
            known = project.site_files_manifest or {}
            manifest = dict(known)
            baseline = None if known else self._last_webblock_code_commit(s, project)  # sites deployed before manifests
            writes: list[FileWrite] = []
            kept: list[str] = []
            for path, content in sorted(target.items()):
                current = self._git(JobState.COMMITTING, self.c.git.read_file, repo, path, head)
                if current is not None and current.content == content:
                    manifest[path] = _sha256(content)
                    continue
                if current is not None:
                    if known:
                        untouched = known.get(path) == _sha256(current.content)
                    else:
                        pushed = self._git(JobState.COMMITTING, self.c.git.read_file, repo, path, baseline) if baseline else None
                        untouched = pushed is not None and pushed.content == current.content
                    if not untouched:
                        kept.append(path)  # edited on GitHub since WebBlock wrote it: never overwritten
                        if path not in manifest and baseline:
                            pushed = self._git(JobState.COMMITTING, self.c.git.read_file, repo, path, baseline)
                            if pushed is not None:
                                manifest[path] = _sha256(pushed.content)
                        continue
                writes.append(FileWrite(path, content))
                manifest[path] = _sha256(content)
            project.site_files_manifest = manifest
            if not writes:
                project.site_code_version = site_code_version()
                job.outcome = JobOutcome.NO_CHANGES.value
                self.jobs.transition(
                    s,
                    job,
                    JobState.SUCCEEDED,
                    "no_changes",
                    "The site already has the latest WebBlock code.",
                    data={"kept_edited_files": kept},
                )
                return Step.CONTINUE
            message = (
                f"Update {project.name} to the latest WebBlock site code\n\n"
                f"Adds/updates {len(writes)} file(s); page content unchanged.\nWebBlock-Job: {job.id}"
            )
            sha = self._git(
                JobState.COMMITTING,
                self.c.git.commit_files,
                repo,
                project.default_branch,
                head,
                writes,
                message,
                create_branch=False,
            )
            self.jobs.event(
                s,
                job,
                "site_code_updated",
                f"Updated {len(writes)} file(s)" + (f"; kept {len(kept)} file(s) you edited on GitHub." if kept else "."),
                data={"written": [w.path for w in writes][:100], "kept_edited_files": kept},
            )
        s.add(
            JobCommit(tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=project.default_branch, kind="merge", status="merged")
        )
        project.source_commit_sha = sha
        project.site_code_version = site_code_version()
        job.base_revision_id = project.published_revision_id
        job.merged_commit_sha = sha
        self.jobs.transition(
            s,
            job,
            JobState.BUILDING,
            "site_pushed",
            f"Pushed the updated site code to {repo.full_name}@{project.default_branch} as {sha[:12]}.",
            data={"commit_sha": sha, "repository": repo.full_name},
        )
        return Step.CONTINUE

    def _manifest(self, files: dict[str, str]) -> dict[str, str]:
        return {p: _sha256(c) for p, c in files.items() if p != self.c.settings.config_repo_path}

    def _last_webblock_code_commit(self, s: Session, project: Project) -> str | None:
        """The commit where WebBlock last pushed the site code (first deploy or a code update)."""
        return s.scalar(
            select(JobCommit.sha)
            .join(Job, Job.id == JobCommit.job_id)
            .where(
                Job.project_id == project.id,
                Job.kind.in_([JobKind.PROVISION.value, JobKind.UPGRADE.value]),
                JobCommit.kind == "merge",
                JobCommit.status.in_(["merged", "deployed"]),
            )
            .order_by(JobCommit.created_at.desc(), JobCommit.id.desc())
            .limit(1)
        )

    def _site_files(self, project: Project, repo_name: str, config: dict[str, Any]) -> dict[str, str]:
        """Code + identity + business-data seed for a new site (Drizzle backend and Business Portal included)."""
        profile = SiteProfile.model_validate(project.profile) if project.profile else SiteProfile(business_name=project.name[:80])
        template = TEMPLATES.get(project.template_id or "")
        return render_site(
            site_name=project.name,
            repo_name=repo_name,
            tenant_id=project.data_tenant_id,
            portal_public_key=self.c.signer().public_key_pem,
            custom_fields=template.custom_fields if template else (),
            seed=seed_data(profile, config),
            config=config,
            config_path=self.c.settings.config_repo_path,
        )

    def _commit_rollback(self, s: Session, job: Job, project: Project) -> Step:
        snap = s.get(Snapshot, job.inputs["snapshot_id"])
        assert snap is not None and snap.project_id == project.id
        repo = self.projects.repo(project)
        job.proposed_revision_id = snap.config_revision_id
        if job.merged_commit_sha:
            self.jobs.transition(s, job, JobState.BUILDING, "reused_restore", "Restore commit already created.")
            return Step.CONTINUE
        head = self._git(JobState.COMMITTING, self.c.git.get_branch_head, repo, project.default_branch)
        differs = bool(
            snap.commit_sha and head and self._git(JobState.COMMITTING, self.c.git.changed_files, repo, snap.commit_sha, head)
        )
        current_snap = s.get(Snapshot, project.last_successful_snapshot_id) if project.last_successful_snapshot_id else None
        deployed_differs = bool(current_snap and snap.commit_sha and current_snap.commit_sha != snap.commit_sha)
        if differs and snap.commit_sha:
            sha = self._git(
                JobState.COMMITTING,
                self.c.git.restore_tree,
                repo,
                project.default_branch,
                head,
                snap.commit_sha,
                f"Restore known-good snapshot {snap.id}\n\nWebBlock-Job: {job.id}\nWebBlock-Restore: {snap.commit_sha}",
            )
            s.add(
                JobCommit(
                    tenant_id=job.tenant_id, job_id=job.id, sha=sha, ref=project.default_branch, kind="restore", status="merged"
                )
            )
            job.merged_commit_sha = sha
            project.source_commit_sha = sha
            self.jobs.transition(
                s,
                job,
                JobState.BUILDING,
                "source_restored",
                f"Default branch restored to the tree of {snap.commit_sha[:12]} as new commit {sha[:12]}.",
            )
        elif deployed_differs:
            job.merged_commit_sha = head
            self.jobs.transition(s, job, JobState.BUILDING, "redeploy_known_good", "Source already matches; redeploying it.")
        elif project.published_revision_id == snap.config_revision_id:
            job.outcome = JobOutcome.NO_CHANGES.value
            self.jobs.transition(s, job, JobState.SUCCEEDED, "no_changes", "The project already matches this snapshot.")
        else:
            self.jobs.transition(
                s, job, JobState.DEPLOYING, "config_only_rollback", "Source unchanged; restoring the configuration only."
            )
        return Step.CONTINUE

    # -- 9: build & deploy ------------------------------------------------------
    def _current_deployment(self, s: Session, job: Job) -> Deployment | None:
        return s.scalar(select(Deployment).where(Deployment.job_id == job.id).order_by(Deployment.attempt.desc()).limit(1))

    def _deploy_request(self, project: Project, job: Job, dep: Deployment) -> DeployRequest:
        created = dep.created_at if dep.created_at.tzinfo else dep.created_at.replace(tzinfo=utcnow().tzinfo)
        return DeployRequest(
            deployment_id=dep.id,
            job_id=job.id,
            repo=self.projects.repo(project),
            ref=project.default_branch,
            commit_sha=dep.commit_sha or "",
            config_hash=self._committed_config_hash(dep),
            created_at=created,
        )

    def _committed_config_hash(self, dep: Deployment) -> str | None:
        """Expected hash of the committed site config (commit delivery only), for deployment verification."""
        if self._delivery() != ConfigDeliveryMode.COMMIT or not dep.config_revision_id:
            return None
        session = object_session(dep)
        rev = session.get(ConfigRevision, dep.config_revision_id) if session else None
        return rev.content_hash if rev else None

    def _apply_state(self, dep: Deployment, state: Any) -> None:
        dep.status = state.status.value
        dep.external_id = state.external_id or dep.external_id
        dep.url = state.url or dep.url
        dep.details = {**dep.details, **state.details}

    def _build(self, s: Session, job: Job) -> Step:
        project = self._project(s, job)
        if not job.merged_commit_sha:
            self.jobs.transition(
                s, job, JobState.DEPLOYING, "no_build_needed", "No source change to build; publishing configuration."
            )
            return Step.CONTINUE
        dep = self._current_deployment(s, job)
        if (
            dep is None
            or dep.status in (DeploymentStatus.FAILED.value, DeploymentStatus.SUPERSEDED.value)
            or dep.provider != self.c.deploy.name
        ):
            dep = Deployment(
                tenant_id=job.tenant_id,
                project_id=project.id,
                job_id=job.id,
                attempt=(dep.attempt + 1) if dep else 1,
                provider=self.c.deploy.name,
                commit_sha=job.merged_commit_sha,
                config_revision_id=job.proposed_revision_id,
                previous_published_revision_id=project.published_revision_id,
                status=DeploymentStatus.PENDING.value,
            )
            s.add(dep)
            s.flush()
            job.deployment_id = dep.id
            self.jobs.event(
                s,
                job,
                "deployment_created",
                f"Deployment attempt {dep.attempt} for {(dep.commit_sha or '')[:12]}.",
                data={"deployment_id": dep.id},
            )
        req = self._deploy_request(project, job, dep)
        try:
            if not dep.details.get("dispatched"):
                state = self.c.deploy.start(req, dep.external_id)
                dep.details = {**dep.details, "dispatched": True}
            else:
                state = self.c.deploy.poll(req, dep.external_id)
        except GitError as exc:
            raise StageFailure(exc.code, exc.message, JobState.BUILDING, retryable=exc.retryable) from exc
        self._apply_state(dep, state)
        if state.status == DeploymentStatus.FAILED:
            dep.finished_at = utcnow()
            raise StageFailure(
                "build_failed",
                "The build/deploy workflow failed; the previous deployment remains live.",
                JobState.BUILDING,
                retryable=True,
                details={"deployment_id": dep.id, "url": dep.url, **state.details},
                hint="Retry creates a new deployment attempt for the same commit; no new commit is made.",
            )
        if state.status in (DeploymentStatus.PENDING, DeploymentStatus.BUILDING):
            if self._deadline_passed(job, JobState.BUILDING, self.c.settings.deploy_timeout_seconds):
                raise StageFailure(
                    "build_timeout",
                    "The build did not finish in time.",
                    JobState.BUILDING,
                    retryable=True,
                    details={"deployment_id": dep.id},
                )
            return Step.WAIT
        self.jobs.transition(
            s, job, JobState.DEPLOYING, "build_finished", "Build finished; deploying.", data={"deployment_id": dep.id}
        )
        return Step.CONTINUE

    def _publish_pointer(self, s: Session, job: Job, project: Project, dep: Deployment) -> None:
        if not job.proposed_revision_id or project.published_revision_id == job.proposed_revision_id:
            return
        dep.previous_published_revision_id = project.published_revision_id
        project.published_revision_id = job.proposed_revision_id
        dep.details = {**dep.details, "published_pointer": True}
        rev = self.projects.revision(s, project, job.proposed_revision_id)
        self._revalidate(project, rev.content_hash, job, dep)

    def _revalidate(self, project: Project, content_hash: str, job: Job, dep: Deployment) -> None:
        if self.c.revalidate is not None:
            self.c.revalidate(project.site_key, content_hash)
            return
        template = self.c.settings.runtime_revalidate_url_template
        if not template:
            return
        secret = self.c.settings.runtime_revalidate_secret
        try:
            resp = httpx.post(
                template.format(site_key=project.site_key, repo_name=project.repo_name),
                json={"config_hash": content_hash, "site_key": project.site_key},
                headers={"Authorization": f"Bearer {secret.get_secret_value()}"} if secret else {},
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise StageFailure(
                "revalidate_failed",
                f"The site's revalidation hook failed: {exc.__class__.__name__}",
                JobState.DEPLOYING,
                retryable=True,
                details={"deployment_id": dep.id},
            ) from exc

    def _deploy(self, s: Session, job: Job) -> Step:
        project = self._project(s, job)
        dep = self._current_deployment(s, job)
        if not job.merged_commit_sha:
            # Path A with runtime delivery (or a configuration-only rollback): publishing = moving the served-revision pointer.
            if (
                dep is None
                or dep.provider != "runtime_config"
                or dep.status in (DeploymentStatus.FAILED.value, DeploymentStatus.SUPERSEDED.value)
            ):
                dep = Deployment(
                    tenant_id=job.tenant_id,
                    project_id=project.id,
                    job_id=job.id,
                    attempt=(dep.attempt + 1) if dep else 1,
                    provider="runtime_config",
                    commit_sha=None,
                    config_revision_id=job.proposed_revision_id,
                    previous_published_revision_id=project.published_revision_id,
                    status=DeploymentStatus.DEPLOYING.value,
                )
                s.add(dep)
                s.flush()
                job.deployment_id = dep.id
            try:
                self._publish_pointer(s, job, project, dep)
            except StageFailure:
                self._restore_pointer(s, project, dep)
                raise
            dep.status = DeploymentStatus.SUCCEEDED.value
            self.jobs.transition(
                s,
                job,
                JobState.VERIFYING,
                "config_published",
                "Runtime configuration published (no source build needed); verifying.",
                data={"deployment_id": dep.id},
            )
            return Step.CONTINUE
        assert dep is not None
        try:
            state = self.c.deploy.poll(self._deploy_request(project, job, dep), dep.external_id)
        except GitError as exc:
            raise StageFailure(exc.code, exc.message, JobState.DEPLOYING, retryable=exc.retryable) from exc
        self._apply_state(dep, state)
        if state.status == DeploymentStatus.FAILED:
            dep.finished_at = utcnow()
            raise StageFailure(
                "deployment_failed",
                "Deployment failed; the previous successful snapshot is unchanged.",
                JobState.DEPLOYING,
                retryable=True,
                details={"deployment_id": dep.id, **state.details},
                hint="Retry runs a new deployment attempt of the same merged commit.",
            )
        if state.status != DeploymentStatus.SUCCEEDED:
            if self._deadline_passed(job, JobState.DEPLOYING, self.c.settings.deploy_timeout_seconds):
                raise StageFailure("deploy_timeout", "Deployment did not finish in time.", JobState.DEPLOYING, retryable=True)
            return Step.WAIT
        # Code is live; only now may configuration that references new components be served.
        try:
            self._publish_pointer(s, job, project, dep)
        except StageFailure:
            self._restore_pointer(s, project, dep)
            raise
        self.jobs.transition(s, job, JobState.VERIFYING, "deployed", "Provider reports success; verifying the live version.")
        return Step.CONTINUE

    def _restore_pointer(self, s: Session, project: Project, dep: Deployment) -> None:
        if dep.details.get("published_pointer") and dep.previous_published_revision_id:
            project.published_revision_id = dep.previous_published_revision_id
            dep.details = {**dep.details, "published_pointer": False, "pointer_restored": True}

    # -- 10: verify & record snapshot -------------------------------------------
    def _verify(self, s: Session, job: Job) -> Step:
        project = self._project(s, job)
        dep = self._current_deployment(s, job)
        assert dep is not None
        checks: dict[str, Any] = {}
        ok = True
        if job.proposed_revision_id:
            rev = self.projects.revision(s, project, job.proposed_revision_id)
            served, served_hash = self.projects.public_config(s, project.site_key)
            config_ok = served_hash == rev.content_hash and config_hash(served) == rev.content_hash
            checks["runtime_config"] = {"ok": config_ok, "served_hash": served_hash, "expected_hash": rev.content_hash}
            ok &= config_ok
        if dep.provider != "runtime_config":
            code_ok, details = self.c.deploy.verify(self._deploy_request(project, job, dep))
            checks["deployed_version"] = {"ok": code_ok, **details}
            ok &= code_ok
        dep.details = {**dep.details, "verification": checks}
        if not ok:
            self._restore_pointer(s, project, dep)
            dep.status = DeploymentStatus.FAILED.value
            dep.finished_at = utcnow()
            raise StageFailure(
                "verification_failed",
                "The deployed version could not be verified; the last successful snapshot is unchanged.",
                JobState.VERIFYING,
                retryable=True,
                details=checks,
                hint="If the platform was slow to propagate, retry verification; otherwise investigate the deployment.",
            )
        dep.status = DeploymentStatus.VERIFIED.value
        dep.finished_at = utcnow()
        self._record_snapshot(s, job, project, dep, checks)
        return Step.CONTINUE

    def _record_snapshot(self, s: Session, job: Job, project: Project, dep: Deployment, checks: dict[str, Any]) -> None:
        prev = s.get(Snapshot, project.last_successful_snapshot_id) if project.last_successful_snapshot_id else None
        registry = self.projects.registry_for(s, project, include_proposed_for_job=job.id)
        rev = s.get(ConfigRevision, project.published_revision_id) if project.published_revision_id else None
        commit_sha: str | None
        if dep.commit_sha:
            commit_sha, commit_source = dep.commit_sha, "deployed"
        elif prev and prev.commit_sha:
            commit_sha, commit_source = prev.commit_sha, "previous_snapshot"
        else:
            commit_sha, commit_source = project.source_commit_sha, "recorded_head_unverified"
        manifest = {
            "tenant_id": project.tenant_id,
            "project_id": project.id,
            "job_id": job.id,
            "job_kind": job.kind,
            "deployment": {
                "id": dep.id,
                "provider": dep.provider,
                "external_id": dep.external_id,
                "attempt": dep.attempt,
                "url": dep.url,
            },
            "config": {
                "revision_id": rev.id if rev else None,
                "revision_number": rev.number if rev else None,
                "hash": rev.content_hash if rev else None,
                "storage_key": rev.storage_key if rev else None,
            },
            "source": {
                "commit_sha": commit_sha,
                "commit_sha_source": commit_source,
                "repo": f"{project.repo_owner}/{project.repo_name}",
                "branch": project.default_branch,
            },
            "registry_hash": registry.hash,
            "plan_hash": job.plan_hash,
            "validation": job.validation,
            "verification": checks,
            "recorded_at": utcnow().isoformat(),
        }
        key, digest = self.c.store.put_json(project.tenant_id, project.id, "snapshots", manifest)
        snap = Snapshot(
            tenant_id=project.tenant_id,
            project_id=project.id,
            job_id=job.id,
            deployment_id=dep.id,
            config_revision_id=rev.id if rev else None,
            config_hash=rev.content_hash if rev else None,
            commit_sha=commit_sha,
            registry_hash=registry.hash,
            manifest_hash=digest,
            manifest_key=key,
            validation=job.validation or {},
            build={"provider": dep.provider, "external_id": dep.external_id, "url": dep.url, "details": dep.details},
            rollback_of_snapshot_id=job.inputs.get("snapshot_id") if job.kind == JobKind.ROLLBACK.value else None,
        )
        s.add(snap)
        s.flush()
        project.last_successful_snapshot_id = snap.id
        job.snapshot_id = snap.id
        if rev and rev.status != "published":
            rev.status = "published"
        for cd in s.scalars(
            select(ComponentDefinition).where(ComponentDefinition.job_id == job.id, ComponentDefinition.status == "proposed")
        ):
            cd.status = "active"
        for jc in s.scalars(select(JobCommit).where(JobCommit.job_id == job.id, JobCommit.status == "merged")):
            jc.status = "deployed"
        self._sync_draft(s, job, project)
        job.outcome = {
            JobKind.ROLLBACK.value: JobOutcome.ROLLED_BACK,
            JobKind.PROVISION.value: JobOutcome.PROVISIONED,
            JobKind.UPGRADE.value: JobOutcome.UPGRADED,
        }.get(job.kind, JobOutcome.DEPLOYED).value
        self.jobs.transition(
            s,
            job,
            JobState.SUCCEEDED,
            "snapshot_recorded",
            f"Verified deployment recorded as snapshot {snap.id}.",
            data={"snapshot_id": snap.id, "manifest_hash": digest},
        )

    def _sync_draft(self, s: Session, job: Job, project: Project) -> None:
        """Keep the editor draft consistent with what was just published, without discarding unrelated draft edits."""
        draft = project.draft_revision_id
        if draft is None or draft == job.base_revision_id or draft == job.inputs.get("draft_revision_id"):
            # The draft had no unpublished edits, or it is exactly what was just published.
            project.draft_revision_id = project.published_revision_id
            project.draft_base_revision_id = project.published_revision_id
            return
        if job.kind != JobKind.CHANGE.value or not job.plan:
            return
        ops = self._plan(job).operations
        try:
            draft_rev = self.projects.revision(s, project, project.draft_revision_id)
            rebased = apply_ops(self.projects.load(project, draft_rev), ops, self.projects.registry_for(s, project))
        except (OpConflict, Exception) as exc:  # noqa: BLE001 - any failure leaves the draft untouched
            self.jobs.event(
                s,
                job,
                "draft_not_rebased",
                f"The open draft has unpublished edits that conflict with this change ({exc}); it was left as is.",
                level="warning",
            )
            return
        new = self.projects.create_revision(
            s, project, rebased, status="draft", created_by=job.created_by, job_id=job.id, parent_id=project.draft_revision_id
        )
        project.draft_revision_id = new.id
        project.draft_base_revision_id = project.published_revision_id
        self.jobs.event(s, job, "draft_rebased", "The open draft was rebased onto the newly published revision.")


def normalize_or_raw(path: str) -> str:
    try:
        return normalize_repo_path(path)
    except PathPolicyError:
        return path

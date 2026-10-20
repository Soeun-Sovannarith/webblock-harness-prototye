from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from webblock_harness.api.deps import ContainerDep, PrincipalDep, SessionDep, WorkerDep
from webblock_harness.api.schemas import ApproveBody, ClarifyBody, EventView, JobView
from webblock_harness.models import Job
from webblock_harness.security.auth import Principal
from webblock_harness.services.container import Container
from webblock_harness.services.errors import NotFound
from webblock_harness.services.jobs import JobService
from webblock_harness.services.worker import Worker

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobView)
def get_job(job_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> JobView:
    return JobView.of(JobService(c).get(s, p, job_id))


@router.get("/{job_id}/events", response_model=list[EventView])
def job_events(job_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> list[EventView]:
    """Structured stage transitions and errors — diagnose a job without reading server logs."""
    return [EventView.of(e) for e in JobService(c).events(s, p, job_id)]


@router.get("/{job_id}/diff", response_class=PlainTextResponse)
def job_diff(job_id: str, s: SessionDep, c: ContainerDep, p: PrincipalDep) -> str:
    """Unified diff of a source-code job's generated patch."""
    job = JobService(c).get(s, p, job_id)
    if not job.diff_key:
        raise NotFound("no_diff", "This job has no source diff.")
    return c.store.get_text(p.tenant_id, job.diff_key)


def _act(c: Container, w: Worker, p: Principal, job_id: str, action: Callable[[Session, JobService], Job]) -> JobView:
    with c.db.session() as s:
        job = action(s, JobService(c))
        resume = job.state == "queued"
    if resume:
        w.submit(job_id)
    with c.db.session() as s:
        return JobView.of(JobService(c).get(s, p, job_id))


@router.post("/{job_id}/approve", response_model=JobView)
def approve(job_id: str, body: ApproveBody, c: ContainerDep, w: WorkerDep, p: PrincipalDep) -> JobView:
    """Approve a high-impact plan. The approval is bound to the plan hash you reviewed."""
    return _act(c, w, p, job_id, lambda s, svc: svc.approve(s, p, job_id, body.plan_hash))


@router.post("/{job_id}/clarify", response_model=JobView)
def clarify(job_id: str, body: ClarifyBody, c: ContainerDep, w: WorkerDep, p: PrincipalDep) -> JobView:
    return _act(c, w, p, job_id, lambda s, svc: svc.clarify(s, p, job_id, body.answers))


@router.post("/{job_id}/cancel", response_model=JobView)
def cancel(job_id: str, c: ContainerDep, w: WorkerDep, p: PrincipalDep) -> JobView:
    return _act(c, w, p, job_id, lambda s, svc: svc.cancel(s, p, job_id))


@router.post("/{job_id}/retry", response_model=JobView)
def retry(job_id: str, c: ContainerDep, w: WorkerDep, p: PrincipalDep) -> JobView:
    """Retry a failed job from its failed stage, when that failure is marked retryable."""
    return _act(c, w, p, job_id, lambda s, svc: svc.retry(s, p, job_id))

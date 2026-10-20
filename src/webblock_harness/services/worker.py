"""Job execution: inline (tests, CLI) or a background thread pool (API server)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from webblock_harness.domain import ACTIVE_STATES, JobState
from webblock_harness.logging_utils import get_logger
from webblock_harness.models import Job
from webblock_harness.services.container import Container
from webblock_harness.services.pipeline import Pipeline

log = get_logger(__name__)
DEFER_DELAY_SECONDS = 5.0


class Worker:
    def __init__(self, c: Container) -> None:
        self.c = c
        self.pipeline = Pipeline(c)
        self.inline = c.settings.job_execution == "inline"
        self._pool = None if self.inline else ThreadPoolExecutor(max_workers=c.settings.job_workers, thread_name_prefix="harness")
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._running:
                return
            self._running.add(job_id)
        if self._pool is None:
            self._run(job_id)
        else:
            self._pool.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        try:
            result = self.pipeline.run(job_id)
        except Exception:  # pragma: no cover - Pipeline.run records failures itself
            log.exception("worker_crashed", extra={"job_id": job_id})
            result = "error"
        finally:
            with self._lock:
                self._running.discard(job_id)
        if result == "deferred" and not self.inline:
            timer = threading.Timer(DEFER_DELAY_SECONDS, self.submit, args=(job_id,))
            timer.daemon = True
            timer.start()
            return
        self._kick_project_queue(job_id)

    def _kick_project_queue(self, job_id: str) -> None:
        """When a job releases its project lock, start the next queued job for that project."""
        with self.c.db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            nxt = s.scalars(
                select(Job.id)
                .where(Job.project_id == job.project_id, Job.state == JobState.QUEUED.value, Job.id != job_id)
                .order_by(Job.created_at)
            ).first()
        if nxt and not self.inline:
            self.submit(nxt)

    def recover(self) -> int:
        """Resume jobs that were in flight when the process stopped (stages are re-entrant)."""
        with self.c.db.session() as s:
            ids = list(
                s.scalars(
                    select(Job.id)
                    .where(Job.state.in_([JobState.QUEUED.value, *[st.value for st in ACTIVE_STATES]]))
                    .order_by(Job.created_at)
                )
            )
        for job_id in ids:
            self.submit(job_id)
        return len(ids)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

from __future__ import annotations

from typing import Any

from webblock_harness.domain import HarnessError, JobState


class ServiceError(Exception):
    status_code = 400

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class NotFound(ServiceError):
    """Also used for resources owned by another tenant, so their existence is not revealed."""

    status_code = 404


class Forbidden(ServiceError):
    status_code = 403


class Conflict(ServiceError):
    status_code = 409


class Unprocessable(ServiceError):
    status_code = 422


class Unavailable(ServiceError):
    status_code = 503


class StageFailure(Exception):
    """Raised inside a pipeline stage to fail the job with a structured, actionable error."""

    def __init__(
        self,
        code: str,
        message: str,
        stage: JobState,
        *,
        retryable: bool = False,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error = HarnessError(code=code, message=message, stage=stage, retryable=retryable, hint=hint, details=details or {})

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from webblock_harness.security.auth import AuthError, Principal, TokenVerifier
from webblock_harness.services.container import Container
from webblock_harness.services.worker import Worker


def container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def worker(request: Request) -> Worker:
    return request.app.state.worker  # type: ignore[no-any-return]


def session(c: Annotated[Container, Depends(container)]) -> Iterator[Session]:
    with c.db.session() as s:
        yield s


def principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
    """Identity comes only from a verified bearer token — never from request bodies, paths or custom headers."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Bearer token required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    verifier: TokenVerifier = request.app.state.verifier
    try:
        return verifier.verify(authorization[7:].strip())
    except AuthError as exc:
        raise HTTPException(
            status_code=401, detail={"code": "invalid_token", "message": str(exc)}, headers={"WWW-Authenticate": "Bearer"}
        ) from exc


ContainerDep = Annotated[Container, Depends(container)]
WorkerDep = Annotated[Worker, Depends(worker)]
SessionDep = Annotated[Session, Depends(session)]
PrincipalDep = Annotated[Principal, Depends(principal)]

"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from webblock_harness.api.routes import jobs, projects, public, sites
from webblock_harness.config import Settings, get_settings
from webblock_harness.logging_utils import configure_logging, get_logger
from webblock_harness.security.auth import TokenVerifier
from webblock_harness.services.container import Container
from webblock_harness.services.errors import ServiceError
from webblock_harness.services.worker import Worker

log = get_logger(__name__)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    settings = container.settings if container else (settings or get_settings())
    owns_container = container is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if owns_container:
            configure_logging(settings.log_level)
        c = container or Container.from_settings(settings)
        app.state.container = c
        app.state.verifier = TokenVerifier(settings)
        app.state.worker = Worker(c)
        if owns_container:
            recovered = app.state.worker.recover()
            log.info("harness_started", extra={"recovered_jobs": recovered, "env": settings.environment})
        yield
        app.state.worker.shutdown()

    app = FastAPI(
        title="WebBlock Harness API",
        version="0.1.0",
        description="Hybrid website-generation harness: runtime Puck configuration for content, controlled source patches for new capabilities.",
        lifespan=lifespan,
    )

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request failed validation.",
                    "details": {"errors": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()][:20]},
                }
            },
        )

    app.include_router(public.router)
    app.include_router(sites.router)
    app.include_router(projects.router)
    app.include_router(jobs.router)

    # The bundled web app (onboarding form, template picker, Puck editor, deploy status).
    static_dir = Path(__file__).resolve().parent.parent / "static" / "app"
    if static_dir.is_dir():
        app.mount("/app", StaticFiles(directory=static_dir, html=True), name="app")

        @app.get("/", include_in_schema=False)
        def _root() -> RedirectResponse:
            return RedirectResponse("/app/")

    return app


def main() -> None:  # pragma: no cover - entry point
    import os
    import sys

    import uvicorn
    from pydantic import ValidationError

    try:
        s = get_settings()
    except ValidationError as exc:
        print("WebBlock Harness configuration error (see .env.example):", file=sys.stderr)
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "settings"
            print(f"  - {field}: {err['msg'].removeprefix('Value error, ')}", file=sys.stderr)
        raise SystemExit(1) from None
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    print(f"WebBlock Harness on http://{host}:{port}  (web app: /app/, API docs: /docs)", file=sys.stderr)
    uvicorn.run(create_app(settings=s), host=host, port=port, log_config=None)

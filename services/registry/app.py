"""The Week 1 empty registry exposes only health and readiness endpoints."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from services.registry.checks import DependencyChecks, LiveDependencyChecks
from services.registry.config import Settings
from services.registry.logging import configure_logging, request_id
from services.registry.probes import ProbeRunner

logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def create_app(
    settings: Settings | None = None, checks: DependencyChecks | None = None
) -> FastAPI:
    settings = settings or Settings()
    probes = checks or LiveDependencyChecks(settings)
    runner = ProbeRunner()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runner.close()

    application = FastAPI(
        title="InGen Fleet Registry Foundation", version="0.1.0", lifespan=lifespan
    )

    @application.middleware("http")
    async def correlate_request(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        correlation = (
            supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
        )
        token = request_id.set(correlation)
        started = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = correlation
            logger.info(
                "%s %s status=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                (perf_counter() - started) * 1000,
            )
            return response
        except Exception:
            logger.exception("Request failed")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "request_id": correlation},
                headers={"X-Request-ID": correlation},
            )
        finally:
            request_id.reset(token)

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def ready() -> JSONResponse:
        dependencies: dict[str, str] = {}

        async def check(name: str, probe: Callable[[], None]) -> None:
            try:
                await runner.run(probe)
                dependencies[name] = "ok"
            except Exception:
                # Never log raw connection errors, which can contain credentials.
                dependencies[name] = "unavailable"
                logger.warning("Readiness dependency unavailable: %s", name)

        checks = (
            ("database", probes.database),
            ("object_store", probes.object_store),
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(*(check(name, probe) for name, probe in checks)),
                timeout=settings.readiness_deadline_seconds,
            )
        except TimeoutError:
            for name, _ in checks:
                dependencies.setdefault(name, "timeout")
            logger.warning("Readiness overall deadline exceeded")
        is_ready = all(value == "ok" for value in dependencies.values())
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={
                "status": "ready" if is_ready else "not_ready",
                "dependencies": dependencies,
            },
        )

    return application


def app_factory() -> FastAPI:
    configure_logging()
    return create_app()

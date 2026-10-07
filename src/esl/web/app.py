from contextlib import asynccontextmanager

from fastapi import FastAPI

from esl import db
from esl.config import settings
from esl.jobs import registry, scheduler
from esl.observability import configure_logging
from esl.web.frontend import mount_frontend
from esl.web.routers import (
    account, catalog, gateway_api, health, label_updates, scan, square, staff, store_status, tags, webhooks,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.assert_rls_enforced(db.engine)
    # registry.JOBS is read here (not at import) so tests and later steps can change it.
    tasks = scheduler.start(registry.JOBS) if settings.jobs_enabled else []
    try:
        yield
    finally:
        await scheduler.stop(tasks)
        await db.engine.dispose()


def create_app(frontend_dist: str | None = None) -> FastAPI:
    configure_logging()
    app = FastAPI(title="ESL Pricing Service", lifespan=lifespan)
    app.include_router(health.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(scan.router, prefix="/v1")
    app.include_router(tags.router, prefix="/v1")
    app.include_router(account.router, prefix="/v1")
    app.include_router(staff.router, prefix="/v1")
    app.include_router(square.router, prefix="/v1")
    app.include_router(webhooks.router, prefix="/v1")
    app.include_router(label_updates.router, prefix="/v1")
    app.include_router(store_status.router, prefix="/v1")
    app.include_router(gateway_api.router)
    mount_frontend(app, settings.frontend_dist if frontend_dist is None else frontend_dist)
    return app


app = create_app()

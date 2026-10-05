from contextlib import asynccontextmanager

from fastapi import FastAPI

from esl import db
from esl.web.routers import account, catalog, health, square, staff, tags, webhooks


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.assert_rls_enforced(db.engine)
    yield
    await db.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="ESL Pricing Service", lifespan=lifespan)
    app.include_router(health.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(tags.router, prefix="/v1")
    app.include_router(account.router, prefix="/v1")
    app.include_router(staff.router, prefix="/v1")
    app.include_router(square.router, prefix="/v1")
    app.include_router(webhooks.router, prefix="/v1")
    return app


app = create_app()

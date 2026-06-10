from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from mmbot.api.routes import router
from mmbot.core.config import get_settings
from mmbot.db.session import Database
from mmbot.observability.logging import configure_logging
from mmbot.redis.manager import RedisManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    app.state.database = Database(settings)
    app.state.redis = RedisManager(settings)
    try:
        yield
    finally:
        await app.state.redis.close()
        await app.state.database.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Institutional Market Making Platform", version="0.2.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("mmbot.api.main:app", host=settings.SERVER_IP, port=settings.SERVER_PORT, reload=False)

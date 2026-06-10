from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.db.session import Database
from mmbot.redis.manager import RedisManager


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_redis(request: Request) -> RedisManager:
    return request.app.state.redis


async def get_session(database: Annotated[Database, Depends(get_database)]) -> AsyncIterator[AsyncSession]:
    async with database.session(actor_service="api") as session:
        yield session

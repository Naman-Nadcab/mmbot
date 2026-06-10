from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from mmbot.core.config import Settings
from mmbot.db.models import AuditLog


class Database:
    def __init__(self, settings: Settings):
        connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
        kwargs = {"pool_pre_ping": True, "connect_args": connect_args}
        if not settings.DATABASE_URL.startswith("sqlite"):
            kwargs.update(pool_size=settings.DB_POOL_SIZE, max_overflow=settings.DB_MAX_OVERFLOW, pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS)
        self.engine: AsyncEngine = create_async_engine(settings.DATABASE_URL, **kwargs)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def session(self, actor_service: str | None = None) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            if actor_service:
                session.sync_session.info["actor_service"] = actor_service
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2))
    async def health_check(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def close(self) -> None:
        await self.engine.dispose()


@event.listens_for(Session, "after_flush")
def audit_flush(session: Session, flush_context) -> None:
    actor_service = session.info.get("actor_service")
    if not actor_service:
        return
    for instance in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(instance, AuditLog):
            continue
        table = getattr(instance, "__tablename__", None)
        identifier = getattr(instance, "id", None)
        if not table or identifier is None:
            continue
        action = "INSERT" if instance in session.new else "DELETE" if instance in session.deleted else "UPDATE"
        session.add(AuditLog(actor_service=actor_service, action=action, resource_type=table, resource_id=identifier, metadata_json={"hook": "after_flush"}))

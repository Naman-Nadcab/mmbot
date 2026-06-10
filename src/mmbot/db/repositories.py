from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.core.config import RuntimeConfig, default_runtime_config
from mmbot.db import models

ModelT = TypeVar("ModelT")


class Repository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, item: ModelT) -> ModelT:
        self.session.add(item)
        await self.session.flush()
        return item

    async def get(self, item_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, item_id)

    async def list(self, statement: Select[tuple[ModelT]] | None = None) -> list[ModelT]:
        result = await self.session.execute(statement or select(self.model))
        return list(result.scalars().all())


class ConfigRepository(Repository[models.BotConfig]):
    model = models.BotConfig

    async def get_latest(self, name: str) -> models.BotConfig | None:
        result = await self.session.execute(select(models.BotConfig).where(models.BotConfig.name == name).order_by(desc(models.BotConfig.version)).limit(1))
        return result.scalar_one_or_none()

    async def upsert_domain(self, domain: str, config: dict[str, Any], actor_user_id: uuid.UUID | None = None) -> models.BotConfig:
        current = await self.get_latest(domain)
        version = 1 if current is None else current.version + 1
        row = models.BotConfig(name=domain, version=version, status=models.BotStatus.enabled, config=config, risk_limits={}, created_by=actor_user_id, approved_by=actor_user_id, approved_at=datetime.now(timezone.utc))
        return await self.add(row)

    async def runtime_config(self) -> RuntimeConfig:
        baseline = default_runtime_config().model_dump()
        for domain in baseline:
            row = await self.get_latest(domain)
            if row is not None:
                baseline[domain] = row.config
        return RuntimeConfig.model_validate(baseline)


class MarketDataRepository(Repository[models.MarketData]):
    model = models.MarketData


class RiskEventRepository(Repository[models.RiskEvent]):
    model = models.RiskEvent


class PositionRepository(Repository[models.Position]):
    model = models.Position

    async def by_account(self, exchange_account_id: uuid.UUID) -> list[models.Position]:
        result = await self.session.execute(select(models.Position).where(models.Position.exchange_account_id == exchange_account_id))
        return list(result.scalars().all())


class InventoryRepository(Repository[models.InventorySnapshot]):
    model = models.InventorySnapshot


class AuditRepository(Repository[models.AuditLog]):
    model = models.AuditLog

    async def record(self, actor_service: str, action: str, resource_type: str, resource_id: uuid.UUID | None, metadata: dict[str, Any]) -> models.AuditLog:
        return await self.add(models.AuditLog(actor_service=actor_service, action=action, resource_type=resource_type, resource_id=resource_id, metadata_json=metadata))

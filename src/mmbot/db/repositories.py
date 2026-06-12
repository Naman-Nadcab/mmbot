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

    async def effective_domain_config(self, domain: str, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
        baseline = default_runtime_config().model_dump()
        if domain not in baseline:
            raise KeyError(domain)
        merged = dict(baseline[domain])
        row = await self.get_latest(domain)
        if row is not None:
            merged = _deep_merge(merged, row.config)
        if overlay is not None:
            merged = _deep_merge(merged, overlay)
        return merged

    async def runtime_config(self) -> RuntimeConfig:
        baseline = default_runtime_config().model_dump()
        for domain in baseline:
            row = await self.get_latest(domain)
            if row is not None:
                baseline[domain] = _deep_merge(baseline[domain], row.config)
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

    async def record(
        self,
        actor_service: str,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID | None,
        metadata: dict[str, Any],
        actor_user_id: uuid.UUID | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> models.AuditLog:
        return await self.add(
            models.AuditLog(
                actor_user_id=actor_user_id,
                actor_service=actor_service,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                before_state=before_state,
                after_state=after_state,
                metadata_json=metadata,
            )
        )


class RuntimeEventRepository(Repository[models.RuntimeEvent]):
    model = models.RuntimeEvent

    async def record(
        self,
        *,
        event_type: str,
        source_component: str,
        status: str,
        payload: dict[str, Any],
        command_id: str | None = None,
        config_domain: str | None = None,
        config_version: int | None = None,
        correlation_id: str | None = None,
        acknowledged_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> models.RuntimeEvent:
        return await self.add(
            models.RuntimeEvent(
                event_type=event_type,
                source_component=source_component,
                status=status,
                command_id=command_id,
                config_domain=config_domain,
                config_version=config_version,
                correlation_id=correlation_id,
                payload=payload,
                acknowledged_at=acknowledged_at,
                metadata_json=metadata or {},
            )
        )

    async def recent(self, limit: int = 100) -> list[models.RuntimeEvent]:
        result = await self.session.execute(select(models.RuntimeEvent).order_by(desc(models.RuntimeEvent.created_at)).limit(limit))
        return list(result.scalars().all())


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged

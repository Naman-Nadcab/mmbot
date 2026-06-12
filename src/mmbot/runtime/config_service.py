from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.db.repositories import AuditRepository, ConfigRepository
from mmbot.redis.manager import RedisManager

RUNTIME_CONFIG_UPDATED_CHANNEL = "runtime.config.updated"
RUNTIME_EVENTS_CHANNEL = "runtime.events"
MARKET_MAKER_COMMAND_CHANNEL = "engine.commands.market-maker-engine"
MARKET_DATA_COMMAND_CHANNEL = "engine.commands.market-data-engine"


class RuntimeConfigService:
    def __init__(self, session: AsyncSession, redis: RedisManager):
        self.session = session
        self.redis = redis

    async def update_domain(self, domain: str, model: type[BaseModel], payload: dict[str, Any], actor: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        repo = ConfigRepository(self.session)
        before = await repo.get_latest(domain)
        validated = model.model_validate(payload).model_dump()
        actor_user_id = _actor_uuid(actor)
        row = await repo.upsert_domain(domain, validated, actor_user_id)
        runtime_config = (await repo.runtime_config()).model_dump()
        audit = await AuditRepository(self.session).record(
            actor_service="api",
            action="CONFIG_UPDATE",
            resource_type="bot_configs",
            resource_id=row.id,
            metadata={"domain": domain, "version": row.version, "actor": actor.get("sub")},
            actor_user_id=actor_user_id,
            before_state=before.config if before is not None else None,
            after_state=validated,
        )
        await self.session.flush()
        event = {
            "event_type": "runtime_config_updated",
            "domain": domain,
            "version": row.version,
            "config": validated,
            "runtime_config": runtime_config,
            "audit_log_id": str(audit.id),
            "actor": actor.get("sub"),
            "published_at": time.time(),
        }
        await self.redis.client.set("runtime:config:latest", _json(event["runtime_config"]))
        await self.redis.client.set(f"runtime:config:{domain}:latest", _json(validated))
        await self.redis.client.publish(RUNTIME_CONFIG_UPDATED_CHANNEL, _json(event))
        await self.redis.client.publish(f"runtime.config.{domain}.updated", _json(event))
        await self.redis.client.publish(RUNTIME_EVENTS_CHANNEL, _json(event))
        await self.redis.client.publish(MARKET_MAKER_COMMAND_CHANNEL, _json({"command_type": "CONFIG_RELOAD", "payload": event, "published_at": time.time()}))
        if domain in {"exchange", "liquidity"}:
            await self.redis.client.publish(MARKET_DATA_COMMAND_CHANNEL, _json({"command_type": "CONFIG_RELOAD", "payload": event, "published_at": time.time()}))
        return row.version, validated


async def publish_runtime_command(
    session: AsyncSession,
    redis: RedisManager,
    *,
    actor: dict[str, Any],
    command_type: str,
    payload: dict[str, Any],
    channel: str = MARKET_MAKER_COMMAND_CHANNEL,
    resource_type: str = "runtime_command",
    action: str | None = None,
) -> dict[str, Any]:
    actor_user_id = _actor_uuid(actor)
    audit = await AuditRepository(session).record(
        actor_service="api",
        action=action or command_type,
        resource_type=resource_type,
        resource_id=None,
        metadata={"command_type": command_type, "actor": actor.get("sub"), **payload},
        actor_user_id=actor_user_id,
        before_state=None,
        after_state=payload,
    )
    await session.flush()
    event = {
        "command_type": command_type,
        "payload": payload | {"audit_log_id": str(audit.id), "actor": actor.get("sub")},
        "published_at": time.time(),
    }
    await redis.client.publish(channel, _json(event))
    await redis.client.publish(RUNTIME_EVENTS_CHANNEL, _json({"event_type": "runtime_command", **event}))
    return event


def _actor_uuid(actor: dict[str, Any]) -> uuid.UUID | None:
    value = actor.get("sub")
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str, separators=(",", ":"))

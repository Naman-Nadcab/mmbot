from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.db.repositories import RuntimeEventRepository
from mmbot.redis.manager import EngineCommunicationLayer

RUNTIME_ACK_CHANNEL = "runtime.acks"


async def publish_runtime_ack(
    session: AsyncSession | None,
    bus: EngineCommunicationLayer,
    *,
    component: str,
    command_id: str | None,
    event_type: str,
    status: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    acknowledged_at = datetime.now(timezone.utc)
    ack = {
        "event_type": event_type,
        "component": component,
        "command_id": command_id,
        "status": status,
        "payload": payload,
        "acknowledged_at": acknowledged_at.isoformat(),
        "published_at": time.time(),
    }
    if session is not None:
        row = await RuntimeEventRepository(session).record(
            event_type=event_type,
            source_component=component,
            status=status,
            command_id=command_id,
            payload=payload,
            acknowledged_at=acknowledged_at,
        )
        ack["runtime_event_id"] = str(row.id)
    if command_id:
        await bus.cache.set_json(f"runtime:ack:{command_id}:{component}", ack, ttl_seconds=86400)
    await bus.pubsub.publish(RUNTIME_ACK_CHANNEL, ack)
    return ack

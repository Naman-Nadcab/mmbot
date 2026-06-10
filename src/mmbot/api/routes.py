from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.api.dependencies import get_database, get_redis, get_session
from mmbot.api.schemas import DOMAIN_MODELS, ConfigResponse, ConfigUpdateRequest, HealthResponse
from mmbot.core.security import require_admin
from mmbot.db.repositories import AuditRepository, ConfigRepository
from mmbot.db.session import Database
from mmbot.exchanges.adapters import supported_exchanges
from mmbot.redis.manager import RedisManager

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(database: Annotated[Database, Depends(get_database)], redis: Annotated[RedisManager, Depends(get_redis)]) -> HealthResponse:
    dependencies: dict[str, str] = {}
    try:
        await database.health_check()
        dependencies["database"] = "healthy"
    except Exception as exc:
        dependencies["database"] = f"unhealthy:{exc.__class__.__name__}"
    try:
        await redis.health_check()
        dependencies["redis"] = "healthy"
    except Exception as exc:
        dependencies["redis"] = f"unhealthy:{exc.__class__.__name__}"
    status_value = "ok" if all(value == "healthy" for value in dependencies.values()) else "degraded"
    return HealthResponse(status=status_value, dependencies=dependencies)


@router.get("/ready", response_model=HealthResponse)
async def ready(database: Annotated[Database, Depends(get_database)], redis: Annotated[RedisManager, Depends(get_redis)]) -> HealthResponse:
    return await health(database, redis)


@router.get("/version")
async def version() -> dict[str, str]:
    from mmbot import __version__
    return {"version": __version__}


@router.get("/admin/config")
async def get_runtime_config(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_admin)]) -> dict:
    repo = ConfigRepository(session)
    return (await repo.runtime_config()).model_dump()


@router.get("/admin/config/{domain}", response_model=ConfigResponse)
async def get_domain_config(domain: str, session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_admin)]) -> ConfigResponse:
    if domain not in DOMAIN_MODELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown configuration domain")
    repo = ConfigRepository(session)
    row = await repo.get_latest(domain)
    if row is None:
        config = (await repo.runtime_config()).model_dump()[domain]
        return ConfigResponse(domain=domain, version=0, config=config)
    return ConfigResponse(domain=domain, version=row.version, config=row.config)


@router.put("/admin/config/{domain}", response_model=ConfigResponse)
async def update_domain_config(domain: str, request: ConfigUpdateRequest, session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_admin)]) -> ConfigResponse:
    model = DOMAIN_MODELS.get(domain)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown configuration domain")
    validated = model.model_validate(request.config).model_dump()
    actor_user_id = uuid.UUID(actor["sub"]) if actor.get("sub") else None
    repo = ConfigRepository(session)
    row = await repo.upsert_domain(domain, validated, actor_user_id)
    await AuditRepository(session).record("api", "CONFIG_UPDATE", "bot_configs", row.id, {"domain": domain, "version": row.version})
    return ConfigResponse(domain=domain, version=row.version, config=row.config)


@router.get("/admin/exchanges/capabilities")
async def exchange_capabilities(_: Annotated[dict, Depends(require_admin)]) -> dict:
    return supported_exchanges()

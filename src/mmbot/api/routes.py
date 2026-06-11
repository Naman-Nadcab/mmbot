from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.api.dependencies import get_database, get_redis, get_session
from mmbot.api.schemas import DOMAIN_MODELS, ConfigResponse, ConfigUpdateRequest, HealthResponse
from mmbot.core.security import require_admin
from mmbot.db import models
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


@router.get("/operations/engines")
async def operations_engines(redis: Annotated[RedisManager, Depends(get_redis)]) -> dict:
    engines: dict[str, object] = {}
    async for key in redis.client.scan_iter(match="engine:health:*"):
        name = str(key).split("engine:health:", 1)[-1]
        engines[name] = await redis.client.get(key)
    return {"engines": {name: _loads_json(value) for name, value in engines.items()}}


@router.get("/operations/orders")
async def operations_orders(session: Annotated[AsyncSession, Depends(get_session)], limit: int = 100) -> dict:
    pairs = await _pair_map(session)
    result = await session.execute(select(models.Order).order_by(desc(models.Order.created_at)).limit(limit))
    return {"items": [_order_payload(row, pairs) for row in result.scalars().all()]}


@router.get("/operations/trades")
async def operations_trades(session: Annotated[AsyncSession, Depends(get_session)], limit: int = 100) -> dict:
    pairs = await _pair_map(session)
    result = await session.execute(select(models.Trade).order_by(desc(models.Trade.traded_at)).limit(limit))
    return {"items": [_trade_payload(row, pairs) for row in result.scalars().all()]}


@router.get("/operations/positions")
async def operations_positions(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    pairs = await _pair_map(session)
    result = await session.execute(select(models.Position).order_by(desc(models.Position.updated_at)))
    return {"items": [_position_payload(row, pairs) for row in result.scalars().all()]}


@router.get("/operations/inventory")
async def operations_inventory(session: Annotated[AsyncSession, Depends(get_session)], limit: int = 100) -> dict:
    result = await session.execute(select(models.InventorySnapshot).order_by(desc(models.InventorySnapshot.captured_at)).limit(limit))
    items = [_inventory_payload(row) for row in result.scalars().all()]
    exposure = sum(float(item.get("valuation_amount") or 0) for item in items)
    return {"items": items, "exposure_notional": exposure, "total_notional": exposure}


@router.get("/operations/pnl")
async def operations_pnl(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    result = await session.execute(select(func.coalesce(func.sum(models.Position.realized_pnl), 0), func.coalesce(func.sum(models.Position.unrealized_pnl), 0)))
    realized, unrealized = result.one()
    realized_f = _float(realized)
    unrealized_f = _float(unrealized)
    return {"realized": realized_f, "unrealized": unrealized_f, "total": realized_f + unrealized_f}


@router.get("/operations/risk-events")
async def operations_risk_events(session: Annotated[AsyncSession, Depends(get_session)], limit: int = 100) -> dict:
    result = await session.execute(select(models.RiskEvent).order_by(desc(models.RiskEvent.occurred_at)).limit(limit))
    return {"items": [_risk_payload(row) for row in result.scalars().all()]}


@router.get("/operations/reconciliation")
async def operations_reconciliation(redis: Annotated[RedisManager, Depends(get_redis)]) -> dict:
    health = _loads_json(await redis.client.get("engine:health:market-maker-engine"))
    counters = (((health or {}).get("runtime") or {}).get("metrics") or {}).get("counters") or {}
    mismatches = int(counters.get("reconciliation.mismatches", 0) or 0)
    alerts = int(counters.get("reconciliation.alerts", 0) or 0)
    runs = int(counters.get("reconciliation.runs", 0) or 0)
    status_value = "ok" if runs > 0 and mismatches == 0 else "warning" if runs > 0 else "not_running"
    return {"status": status_value, "runs": runs, "mismatch_count": mismatches, "alert_count": alerts, "mismatches": []}


async def _pair_map(session: AsyncSession) -> dict[uuid.UUID, str]:
    result = await session.execute(select(models.TradingPair))
    return {row.id: row.normalized_symbol for row in result.scalars().all()}


def _order_payload(row: models.Order, pairs: dict[uuid.UUID, str]) -> dict:
    return {
        "id": str(row.id),
        "client_order_id": row.client_order_id,
        "exchange_order_id": row.exchange_order_id,
        "symbol": pairs.get(row.trading_pair_id, str(row.trading_pair_id)),
        "side": row.side.value,
        "type": row.order_type.value,
        "status": row.status.value,
        "price": _float(row.price),
        "quantity": _float(row.quantity),
        "filled_quantity": _float(row.filled_quantity),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _trade_payload(row: models.Trade, pairs: dict[uuid.UUID, str]) -> dict:
    return {
        "id": str(row.id),
        "trade_id": row.exchange_trade_id,
        "symbol": pairs.get(row.trading_pair_id, str(row.trading_pair_id)),
        "side": row.side.value,
        "price": _float(row.price),
        "quantity": _float(row.quantity),
        "fee": _float(row.fee_amount),
        "fee_asset": row.fee_asset,
        "traded_at": _iso(row.traded_at),
    }


def _position_payload(row: models.Position, pairs: dict[uuid.UUID, str]) -> dict:
    return {
        "id": str(row.id),
        "symbol": pairs.get(row.trading_pair_id, str(row.trading_pair_id)),
        "asset": row.asset,
        "side": row.side.value,
        "quantity": _float(row.quantity),
        "notional": _float(row.quantity) * _float(row.mark_price),
        "realized_pnl": _float(row.realized_pnl),
        "unrealized_pnl": _float(row.unrealized_pnl),
        "mark_price": _float(row.mark_price),
        "updated_at": _iso(row.updated_at),
    }


def _inventory_payload(row: models.InventorySnapshot) -> dict:
    return {
        "id": str(row.id),
        "asset": row.asset,
        "total_balance": _float(row.total_balance),
        "available_balance": _float(row.available_balance),
        "reserved_balance": _float(row.reserved_balance),
        "valuation_asset": row.valuation_asset,
        "valuation_price": _float(row.valuation_price),
        "valuation_amount": _float(row.valuation_amount),
        "captured_at": _iso(row.captured_at),
    }


def _risk_payload(row: models.RiskEvent) -> dict:
    return {
        "id": str(row.id),
        "severity": row.severity.value,
        "event_type": row.event_type,
        "source_component": row.source_component,
        "message": row.message,
        "occurred_at": _iso(row.occurred_at),
    }


def _float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _loads_json(value: object) -> object:
    if value is None:
        return None
    import json

    return json.loads(value if isinstance(value, str) else value.decode())

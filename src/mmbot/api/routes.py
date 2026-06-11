from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
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


@router.get("/operations/infrastructure")
async def operations_infrastructure(database: Annotated[Database, Depends(get_database)], redis: Annotated[RedisManager, Depends(get_redis)]) -> dict:
    db_started = time.perf_counter()
    database_ok = await database.health_check()
    db_latency_ms = (time.perf_counter() - db_started) * 1000
    redis_started = time.perf_counter()
    redis_ok = await redis.health_check()
    redis_latency_ms = (time.perf_counter() - redis_started) * 1000
    return {
        "database": "healthy" if database_ok else "unhealthy",
        "redis": "healthy" if redis_ok else "unhealthy",
        "database_latency_ms": round(db_latency_ms, 3),
        "redis_latency_ms": round(redis_latency_ms, 3),
    }


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


@router.websocket("/ws/operations")
async def operations_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    database: Database = websocket.app.state.database
    redis: RedisManager = websocket.app.state.redis
    last_seen: dict[str, object] = {"orders": set(), "trades": set(), "risk_events": set(), "risk_approvals": 0, "risk_rejections": 0, "reconciliation_runs": 0, "reconnect_count": 0}
    try:
        while True:
            async with database.session(actor_service="operations-ws") as session:
                await _send_operation_events(websocket, session, redis, last_seen)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


async def _send_operation_events(websocket: WebSocket, session: AsyncSession, redis: RedisManager, last_seen: dict[str, object]) -> None:
    pairs = await _pair_map(session)
    engine_payload = (await operations_engines(redis))["engines"]
    await websocket.send_text(json.dumps({"type": "engine_health", "payload": {"engines": engine_payload}}, default=str))

    maker_health = _loads_json(await redis.client.get("engine:health:market-maker-engine")) or {}
    data_health = _loads_json(await redis.client.get("engine:health:market-data-engine")) or {}
    counters = (((maker_health.get("runtime") or {}).get("metrics") or {}).get("counters") or {})
    data_counters = (((data_health.get("runtime") or {}).get("metrics") or {}).get("counters") or {})

    risk_approvals = _int_counter(counters.get("risk.approvals"))
    if risk_approvals > int(last_seen["risk_approvals"]):
        await websocket.send_text(json.dumps({"type": "risk_approved", "payload": {"count": risk_approvals}}, default=str))
        last_seen["risk_approvals"] = risk_approvals

    risk_rejections = _int_counter(counters.get("risk.rejections"))
    if risk_rejections > int(last_seen["risk_rejections"]):
        await websocket.send_text(json.dumps({"type": "risk_rejected", "payload": {"count": risk_rejections}}, default=str))
        last_seen["risk_rejections"] = risk_rejections

    reconciliation_runs = _int_counter(counters.get("reconciliation.runs"))
    if reconciliation_runs > int(last_seen["reconciliation_runs"]):
        await websocket.send_text(json.dumps({"type": "reconciliation_completed", "payload": await operations_reconciliation(redis)}, default=str))
        last_seen["reconciliation_runs"] = reconciliation_runs

    reconnect_count = _int_counter(data_counters.get("market_data.reconnect_count"))
    if reconnect_count > int(last_seen["reconnect_count"]):
        await websocket.send_text(json.dumps({"type": "websocket_reconnected", "payload": {"count": reconnect_count}}, default=str))
        last_seen["reconnect_count"] = reconnect_count

    order_result = await session.execute(select(models.Order).order_by(desc(models.Order.created_at)).limit(50))
    orders = [_order_payload(row, pairs) for row in order_result.scalars().all()]
    known_orders: set[str] = last_seen["orders"]  # type: ignore[assignment]
    new_orders = [order for order in orders if order["id"] not in known_orders]
    if new_orders:
        for order in new_orders:
            await websocket.send_text(json.dumps({"type": "order_created", "payload": order}, default=str))
            known_orders.add(order["id"])
        await websocket.send_text(json.dumps({"type": "orders", "payload": {"items": orders}}, default=str))

    trade_result = await session.execute(select(models.Trade).order_by(desc(models.Trade.traded_at)).limit(50))
    trades = [_trade_payload(row, pairs) for row in trade_result.scalars().all()]
    known_trades: set[str] = last_seen["trades"]  # type: ignore[assignment]
    new_trades = [trade for trade in trades if trade["id"] not in known_trades]
    if new_trades:
        for trade in new_trades:
            await websocket.send_text(json.dumps({"type": "order_filled", "payload": trade}, default=str))
            known_trades.add(trade["id"])
        await websocket.send_text(json.dumps({"type": "trades", "payload": {"items": trades}}, default=str))

    risk_result = await session.execute(select(models.RiskEvent).order_by(desc(models.RiskEvent.occurred_at)).limit(50))
    risk_events = [_risk_payload(row) for row in risk_result.scalars().all()]
    known_risks: set[str] = last_seen["risk_events"]  # type: ignore[assignment]
    for event in risk_events:
        if event["id"] not in known_risks:
            await websocket.send_text(json.dumps({"type": "risk_events", "payload": {"items": risk_events}}, default=str))
            known_risks.add(event["id"])
            break

    positions = (await operations_positions(session))["items"]
    inventory = await operations_inventory(session)
    pnl = await operations_pnl(session)
    await websocket.send_text(json.dumps({"type": "positions", "payload": {"items": positions}}, default=str))
    await websocket.send_text(json.dumps({"type": "inventory", "payload": inventory}, default=str))
    await websocket.send_text(json.dumps({"type": "pnl", "payload": pnl}, default=str))


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


def _int_counter(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0

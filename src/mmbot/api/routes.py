from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

import websockets
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.api.dependencies import get_database, get_redis, get_session
from mmbot.api.schemas import DOMAIN_MODELS, ConfigResponse, ConfigUpdateRequest, ConfirmedActionRequest, ExchangeAccountCreateRequest, ExchangeAccountStatusRequest, ExchangeConnectRequest, ExchangeRemoveRequest, ExchangeTestRequest, HealthResponse, StrategyCommandRequest
from mmbot.core.config import get_settings
from mmbot.core.security import decode_token, require_admin, require_config_write, require_incident_response, require_operations_access, require_risk_write, require_trading_control
from mmbot.db import models
from mmbot.db.repositories import AuditRepository, ConfigRepository, RuntimeEventRepository
from mmbot.engines.volume.engine import VolumeEngine
from mmbot.db.session import Database
from mmbot.exchanges.adapters import create_adapter, supported_exchanges
from mmbot.exchanges.auth import Credentials, HmacSigner
from mmbot.exchanges.client import RestClient, WebSocketClient
from mmbot.exchanges.registry import get_exchange_definition
from mmbot.execution.client import PrivateRestExecutionClient
from mmbot.execution.models import ExecutionVenue
from mmbot.redis.manager import RedisManager
from mmbot.runtime.config_service import MARKET_DATA_COMMAND_CHANNEL, MARKET_MAKER_COMMAND_CHANNEL, RuntimeConfigService, publish_runtime_command
from mmbot.security.secrets import SecretCipher
from mmbot.execution.coinstore_ws import CoinstorePrivateWebSocketClient
from mmbot.execution.signing import ExecutionCredentials

router = APIRouter()
logger = logging.getLogger(__name__)


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
        config = await repo.effective_domain_config(domain)
        return ConfigResponse(domain=domain, version=0, config=config)
    return ConfigResponse(domain=domain, version=row.version, config=await repo.effective_domain_config(domain))


@router.put("/admin/config/{domain}", response_model=ConfigResponse)
async def update_domain_config(domain: str, request: ConfigUpdateRequest, session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_config_write)]) -> ConfigResponse:
    model = DOMAIN_MODELS.get(domain)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown configuration domain")
    _require_config_domain_actor(domain, actor)
    version, config = await RuntimeConfigService(session, redis).update_domain(domain, model, request.config, actor)
    return ConfigResponse(domain=domain, version=version, config=config)


@router.get("/admin/exchanges/capabilities")
async def exchange_capabilities(_: Annotated[dict, Depends(require_admin)]) -> dict:
    return supported_exchanges()


@router.get("/exchanges")
async def exchanges(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], settings=Depends(get_settings)) -> dict:
    accounts = await _exchange_accounts(session)
    by_exchange: dict[str, list[models.ExchangeAccount]] = {}
    for account in accounts:
        by_exchange.setdefault(account.exchange_name, []).append(account)
    return {
        "items": [
            {
                "exchange_name": name,
                "capabilities": definition,
                "accounts": [_exchange_account_payload(row, settings) for row in by_exchange.get(name, [])],
                "status": _aggregate_exchange_status(by_exchange.get(name, [])),
            }
            for name, definition in supported_exchanges().items()
        ]
    }


@router.get("/exchanges/status")
async def exchanges_status(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], settings=Depends(get_settings)) -> dict:
    accounts = await _exchange_accounts(session)
    return {"items": [_exchange_account_payload(row, settings) for row in accounts]}


@router.post("/exchanges/connect")
async def exchanges_connect(request: ExchangeConnectRequest, session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_config_write)], settings=Depends(get_settings)) -> dict:
    if request.exchange_name not in supported_exchanges():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unsupported exchange")
    cipher = SecretCipher(settings)
    result = await session.execute(select(models.ExchangeAccount).where(models.ExchangeAccount.exchange_name == request.exchange_name, models.ExchangeAccount.account_alias == request.account_alias, models.ExchangeAccount.environment == request.environment))
    row = result.scalar_one_or_none()
    before = _exchange_account_payload(row, settings) if row is not None else None
    if row is None:
        row = models.ExchangeAccount(exchange_name=request.exchange_name, account_alias=request.account_alias, environment=request.environment, api_key_ciphertext=cipher.encrypt(request.api_key) or b"", api_secret_ciphertext=cipher.encrypt(request.api_secret) or b"", passphrase_ciphertext=cipher.encrypt(request.passphrase), encryption_key_id=cipher.key_id, permissions=request.permissions, is_enabled=request.enabled)
        session.add(row)
    else:
        row.api_key_ciphertext = cipher.encrypt(request.api_key) or b""
        row.api_secret_ciphertext = cipher.encrypt(request.api_secret) or b""
        row.passphrase_ciphertext = cipher.encrypt(request.passphrase)
        row.encryption_key_id = cipher.key_id
        row.permissions = request.permissions
        row.is_enabled = request.enabled
        row.connection_status = "disconnected"
        row.rest_connected = False
        row.websocket_connected = False
        row.private_ws_connected = False
        row.last_error_message = None
    await session.flush()
    payload = _exchange_account_payload(row, settings)
    await AuditRepository(session).record("api", "EXCHANGE_CONNECT", "exchange_accounts", row.id, {"actor": actor.get("sub"), "exchange_name": row.exchange_name, "account_alias": row.account_alias}, before_state=before, after_state=payload)
    await publish_runtime_command(session, redis, actor=actor, command_type="EXCHANGE_ACCOUNT_UPDATED", payload={"account_id": str(row.id), "exchange_name": row.exchange_name, "account_alias": row.account_alias}, channel=MARKET_DATA_COMMAND_CHANNEL, resource_type="exchange_accounts")
    return payload


@router.post("/exchanges/test")
async def exchanges_test(request: ExchangeTestRequest, session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_config_write)], settings=Depends(get_settings)) -> dict:
    row = await _exchange_account(session, request.exchange_name, request.account_alias, request.environment)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="exchange account not found")
    result = await _test_exchange_connection(row, settings)
    await _apply_exchange_test_result(row, result)
    await session.flush()
    await session.refresh(row)
    return _exchange_account_payload(row, settings) | {"test_result": result}


@router.delete("/exchanges/remove")
async def exchanges_remove(request: ExchangeRemoveRequest, session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_config_write)], settings=Depends(get_settings)) -> dict:
    _require_confirmation(request.confirmation, "remove")
    row = await _exchange_account(session, request.exchange_name, request.account_alias, request.environment)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="exchange account not found")
    before = _exchange_account_payload(row, settings)
    await session.delete(row)
    await AuditRepository(session).record("api", "EXCHANGE_REMOVE", "exchange_accounts", row.id, {"actor": actor.get("sub"), "exchange_name": request.exchange_name, "account_alias": request.account_alias}, before_state=before, after_state=None)
    await publish_runtime_command(session, redis, actor=actor, command_type="EXCHANGE_ACCOUNT_REMOVED", payload={"exchange_name": request.exchange_name, "account_alias": request.account_alias}, channel=MARKET_DATA_COMMAND_CHANNEL, resource_type="exchange_accounts")
    return {"removed": True, "exchange_name": request.exchange_name, "account_alias": request.account_alias}


@router.get("/operations/runtime-config")
async def operations_runtime_config(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    return (await ConfigRepository(session).runtime_config()).model_dump()


@router.get("/operations/audit-logs")
async def operations_audit_logs(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], limit: int = 100) -> dict:
    result = await session.execute(select(models.AuditLog).order_by(desc(models.AuditLog.occurred_at)).limit(limit))
    return {"items": [_audit_payload(row) for row in result.scalars().all()]}


@router.get("/operations/runtime-events")
async def operations_runtime_events(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], limit: int = 100) -> dict:
    return {"items": [_runtime_event_payload(row) for row in await RuntimeEventRepository(session).recent(limit)]}


@router.post("/admin/strategy/command")
async def admin_strategy_command(request: StrategyCommandRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_trading_control)]) -> dict:
    _require_confirmation(request.confirmation, request.command)
    state = {
        "state": "running" if request.command in {"start", "resume"} else "paused" if request.command == "pause" else "stopped",
        "reason": request.reason,
        "actor": actor.get("sub"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis.client.set("runtime:strategy:state", json.dumps(state, separators=(",", ":")))
    event = await publish_runtime_command(session, redis, actor=actor, command_type="STRATEGY_COMMAND", payload={"action": request.command, "reason": request.reason, "state": state}, action=f"STRATEGY_{request.command.upper()}")
    return {"state": state, "event": event}


@router.get("/operations/volume")
async def operations_volume(session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    config = await ConfigRepository(session).runtime_config()
    progress = await _volume_progress_payload(session, redis, config.volume.model_dump())
    return progress


@router.get("/admin/coinstore/accounts")
async def coinstore_accounts(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    result = await session.execute(select(models.ExchangeAccount).where(models.ExchangeAccount.exchange_name == "coinstore").order_by(desc(models.ExchangeAccount.updated_at)))
    return {"items": [_exchange_account_payload(row) for row in result.scalars().all()]}


@router.post("/admin/coinstore/accounts")
async def coinstore_account_upsert(request: ExchangeAccountCreateRequest, session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_config_write)], settings=Depends(get_settings)) -> dict:
    cipher = SecretCipher(settings)
    result = await session.execute(select(models.ExchangeAccount).where(models.ExchangeAccount.exchange_name == "coinstore", models.ExchangeAccount.account_alias == request.account_alias, models.ExchangeAccount.environment == request.environment))
    row = result.scalar_one_or_none()
    before = _exchange_account_payload(row) if row is not None else None
    if row is None:
        row = models.ExchangeAccount(exchange_name="coinstore", account_alias=request.account_alias, environment=request.environment, api_key_ciphertext=cipher.encrypt(request.api_key) or b"", api_secret_ciphertext=cipher.encrypt(request.api_secret) or b"", passphrase_ciphertext=cipher.encrypt(request.passphrase), encryption_key_id=cipher.key_id, permissions=request.permissions, is_enabled=request.is_enabled)
        session.add(row)
    else:
        row.api_key_ciphertext = cipher.encrypt(request.api_key) or b""
        row.api_secret_ciphertext = cipher.encrypt(request.api_secret) or b""
        row.passphrase_ciphertext = cipher.encrypt(request.passphrase)
        row.encryption_key_id = cipher.key_id
        row.permissions = request.permissions
        row.is_enabled = request.is_enabled
    await session.flush()
    payload = _exchange_account_payload(row)
    await AuditRepository(session).record("api", "COINSTORE_ACCOUNT_UPSERT", "exchange_accounts", row.id, {"actor": actor.get("sub"), "account_alias": row.account_alias}, before_state=before, after_state=payload)
    await publish_runtime_command(session, redis, actor=actor, command_type="COINSTORE_ACCOUNT_UPDATED", payload={"account_id": str(row.id), "account_alias": row.account_alias}, channel=MARKET_DATA_COMMAND_CHANNEL, resource_type="exchange_accounts")
    return payload


@router.put("/admin/coinstore/accounts/{account_id}/status")
async def coinstore_account_status(account_id: uuid.UUID, request: ExchangeAccountStatusRequest, session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_config_write)]) -> dict:
    _require_confirmation(request.confirmation, "enable" if request.is_enabled else "disable")
    row = await session.get(models.ExchangeAccount, account_id)
    if row is None or row.exchange_name != "coinstore":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="coinstore account not found")
    before = _exchange_account_payload(row)
    row.is_enabled = request.is_enabled
    await session.flush()
    payload = _exchange_account_payload(row)
    await AuditRepository(session).record("api", "COINSTORE_ACCOUNT_STATUS", "exchange_accounts", row.id, {"actor": actor.get("sub"), "reason": request.reason}, before_state=before, after_state=payload)
    await publish_runtime_command(session, redis, actor=actor, command_type="COINSTORE_ACCOUNT_STATUS", payload={"account_id": str(row.id), "is_enabled": row.is_enabled, "reason": request.reason}, channel=MARKET_DATA_COMMAND_CHANNEL, resource_type="exchange_accounts")
    return payload


@router.get("/admin/coinstore/health")
async def coinstore_health(redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_operations_access)], settings=Depends(get_settings)) -> dict:
    adapter = create_adapter("coinstore", settings, "coinstore")
    rest_status = "unknown"
    rest_error = None
    started = time.perf_counter()
    try:
        await adapter.rest.health()
        rest_status = "healthy"
    except Exception as exc:
        rest_status = "unhealthy"
        rest_error = f"{exc.__class__.__name__}: {exc}"
    finally:
        await adapter.close()
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    data_health = _loads_json(await redis.client.get("engine:health:market-data-engine")) or {}
    runtime = data_health.get("runtime") or {}
    last_messages = runtime.get("last_message_timestamp") or {}
    websocket_keys = [key for key in last_messages if str(key).startswith("coinstore:")]
    definition = supported_exchanges()["coinstore"]
    return {
        "rest": {"status": rest_status, "latency_ms": latency_ms, "error": rest_error},
        "websocket": {"status": "healthy" if websocket_keys else runtime.get("websocket_state", "no_messages"), "last_message_keys": websocket_keys},
        "rate_limit": definition["rate_limit"],
        "actor": actor.get("sub"),
    }


@router.post("/admin/coinstore/balance-sync")
async def coinstore_balance_sync(request: ConfirmedActionRequest, session: Annotated[AsyncSession, Depends(get_session)], redis: Annotated[RedisManager, Depends(get_redis)], actor: Annotated[dict, Depends(require_config_write)], settings=Depends(get_settings)) -> dict:
    _require_confirmation(request.confirmation, "sync")
    result = await session.execute(select(models.ExchangeAccount).where(models.ExchangeAccount.exchange_name == "coinstore", models.ExchangeAccount.is_enabled.is_(True)).order_by(desc(models.ExchangeAccount.updated_at)).limit(1))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no enabled coinstore account")
    cipher = SecretCipher(settings)
    api_key = cipher.decrypt(account.api_key_ciphertext)
    api_secret = cipher.decrypt(account.api_secret_ciphertext)
    if not api_key or not api_secret:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="coinstore account is missing credentials")
    adapter = create_adapter("coinstore", settings, "coinstore")
    try:
        adapter.rest.signer = HmacSigner(Credentials(api_key, api_secret, cipher.decrypt(account.passphrase_ciphertext)))
        raw = await adapter.rest_request("GET", "/api/spot/accountList", signed=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"coinstore balance sync failed: {exc}") from exc
    finally:
        await adapter.close()
    balances = _parse_balance_rows(raw)
    captured_at = datetime.now(timezone.utc)
    rows_written = 0
    for item in balances:
        session.add(models.InventorySnapshot(exchange_account_id=account.id, asset=item["asset"], total_balance=Decimal(str(item["total"])), available_balance=Decimal(str(item["available"])), reserved_balance=Decimal(str(item["reserved"])), valuation_asset="USDT", valuation_price=None, valuation_amount=None, captured_at=captured_at, metadata_json={"source": "coinstore_balance_sync"}))
        rows_written += 1
    await AuditRepository(session).record("api", "COINSTORE_BALANCE_SYNC", "inventory_snapshots", account.id, {"actor": actor.get("sub"), "rows_written": rows_written, "reason": request.reason})
    await publish_runtime_command(session, redis, actor=actor, command_type="COINSTORE_BALANCE_SYNC", payload={"account_id": str(account.id), "rows_written": rows_written}, channel=MARKET_DATA_COMMAND_CHANNEL, resource_type="inventory_snapshots")
    return {"rows_written": rows_written, "captured_at": captured_at.isoformat()}


@router.get("/operations/engines")
async def operations_engines(redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    engines: dict[str, object] = {}
    async for key in redis.client.scan_iter(match="engine:health:*"):
        name = str(key).split("engine:health:", 1)[-1]
        engines[name] = await redis.client.get(key)
    return {"engines": {name: _loads_json(value) for name, value in engines.items()}}


@router.get("/operations/infrastructure")
async def operations_infrastructure(database: Annotated[Database, Depends(get_database)], redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
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


@router.get("/operations/exchanges")
async def operations_exchanges(redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    data_health = _loads_json(await redis.client.get("engine:health:market-data-engine")) or {}
    runtime = data_health.get("runtime") or {}
    last_messages = runtime.get("last_message_timestamp") or {}
    active_subscriptions = int(runtime.get("active_subscriptions") or 0)
    websocket_state = runtime.get("websocket_state") or "unknown"
    exchanges: dict[str, dict[str, object]] = {}
    for key, timestamp in last_messages.items():
        exchange = str(key).split(":", 1)[0]
        exchanges.setdefault(exchange, {"exchange": exchange, "status": "connected", "symbols": [], "last_message_timestamp": timestamp, "websocket_state": websocket_state, "active_subscriptions": active_subscriptions})
        if ":" in str(key):
            exchanges[exchange]["symbols"].append(str(key).split(":", 1)[1])
        exchanges[exchange]["last_message_timestamp"] = timestamp
    for name in supported_exchanges():
        exchanges.setdefault(name, {"exchange": name, "status": "configured_no_messages", "symbols": [], "last_message_timestamp": None, "websocket_state": websocket_state, "active_subscriptions": active_subscriptions})
    return {"exchanges": exchanges}


@router.get("/operations/orders")
async def operations_orders(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], limit: int = 100) -> dict:
    pairs = await _pair_map(session)
    result = await session.execute(select(models.Order).order_by(desc(models.Order.created_at)).limit(limit))
    return {"items": [_order_payload(row, pairs) for row in result.scalars().all()]}


@router.get("/operations/trades")
async def operations_trades(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], limit: int = 100) -> dict:
    pairs = await _pair_map(session)
    result = await session.execute(select(models.Trade).order_by(desc(models.Trade.traded_at)).limit(limit))
    return {"items": [_trade_payload(row, pairs) for row in result.scalars().all()]}


@router.get("/operations/positions")
async def operations_positions(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    pairs = await _pair_map(session)
    result = await session.execute(select(models.Position).order_by(desc(models.Position.updated_at)))
    return {"items": [_position_payload(row, pairs) for row in result.scalars().all()]}


@router.get("/operations/inventory")
async def operations_inventory(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], limit: int = 100) -> dict:
    result = await session.execute(select(models.InventorySnapshot).order_by(desc(models.InventorySnapshot.captured_at)).limit(limit))
    items = [_inventory_payload(row) for row in result.scalars().all()]
    exposure = sum(float(item.get("valuation_amount") or 0) for item in items)
    return {"items": items, "exposure_notional": exposure, "total_notional": exposure}


@router.get("/operations/pnl")
async def operations_pnl(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    result = await session.execute(select(func.coalesce(func.sum(models.Position.realized_pnl), 0), func.coalesce(func.sum(models.Position.unrealized_pnl), 0)))
    realized, unrealized = result.one()
    realized_f = _float(realized)
    unrealized_f = _float(unrealized)
    return {"realized": realized_f, "unrealized": unrealized_f, "total": realized_f + unrealized_f}


@router.get("/operations/risk-events")
async def operations_risk_events(session: Annotated[AsyncSession, Depends(get_session)], _: Annotated[dict, Depends(require_operations_access)], limit: int = 100) -> dict:
    result = await session.execute(select(models.RiskEvent).order_by(desc(models.RiskEvent.occurred_at)).limit(limit))
    return {"items": [_risk_payload(row) for row in result.scalars().all()]}


@router.get("/operations/reconciliation")
async def operations_reconciliation(redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    health = _loads_json(await redis.client.get("engine:health:market-maker-engine"))
    counters = (((health or {}).get("runtime") or {}).get("metrics") or {}).get("counters") or {}
    mismatches = int(counters.get("reconciliation.mismatches", 0) or 0)
    alerts = int(counters.get("reconciliation.alerts", 0) or 0)
    runs = int(counters.get("reconciliation.runs", 0) or 0)
    status_value = "ok" if runs > 0 and mismatches == 0 else "warning" if runs > 0 else "not_running"
    return {"status": status_value, "runs": runs, "mismatch_count": mismatches, "alert_count": alerts, "mismatches": []}


@router.get("/operations/kill-switch")
async def operations_kill_switch_status(redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_operations_access)]) -> dict:
    state = _loads_json(await redis.client.get("risk:kill_switch")) or {"active": False}
    return state


@router.get("/admin/kill-switch/status")
async def admin_kill_switch_status(redis: Annotated[RedisManager, Depends(get_redis)], _: Annotated[dict, Depends(require_admin)]) -> dict:
    state = _loads_json(await redis.client.get("risk:kill_switch")) or {"active": False}
    return state


@router.post("/admin/kill-switch/enable")
async def admin_kill_switch_enable(request: dict, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_admin)]) -> dict:
    reason = str(request.get("reason") or "operator_request")
    state = {"active": True, "reason": reason, "actor": actor.get("sub"), "activated_at": datetime.now(timezone.utc).isoformat()}
    await redis.client.set("risk:kill_switch", json.dumps(state, separators=(",", ":")))
    session.add(models.RiskEvent(severity=models.RiskSeverity.critical, event_type="KILL_SWITCH_ACTIVATED", source_component="operations-api", message=reason, is_kill_switch_triggered=True, metadata_json={"actor": actor.get("sub")}))
    return state


@router.post("/admin/kill-switch/disable")
async def admin_kill_switch_disable(redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_admin)]) -> dict:
    await redis.client.delete("risk:kill_switch")
    session.add(models.RiskEvent(severity=models.RiskSeverity.high, event_type="KILL_SWITCH_CLEARED", source_component="operations-api", message="operator_clear", metadata_json={"actor": actor.get("sub")}))
    return {"active": False}


@router.post("/admin/emergency/cancel-all-orders")
async def emergency_cancel_all_orders(request: ConfirmedActionRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_incident_response)]) -> dict:
    _require_confirmation(request.confirmation, "cancel")
    event = await publish_runtime_command(session, redis, actor=actor, command_type="CANCEL_ALL_ORDERS", payload={"reason": request.reason, "confirmed": True}, action="EMERGENCY_CANCEL_ALL_ORDERS")
    session.add(models.RiskEvent(severity=models.RiskSeverity.high, event_type="EMERGENCY_CANCEL_ALL_ORDERS", source_component="operations-api", message=request.reason, metadata_json={"actor": actor.get("sub")}))
    return {"accepted": True, "event": event}


@router.post("/admin/emergency/disable-trading")
async def emergency_disable_trading(request: ConfirmedActionRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_incident_response)]) -> dict:
    _require_confirmation(request.confirmation, "disable")
    state = {"active": True, "reason": request.reason, "actor": actor.get("sub"), "activated_at": datetime.now(timezone.utc).isoformat()}
    await redis.client.set("risk:kill_switch", json.dumps(state, separators=(",", ":")))
    event = await publish_runtime_command(session, redis, actor=actor, command_type="DISABLE_TRADING", payload={"reason": request.reason, "kill_switch": state}, action="EMERGENCY_DISABLE_TRADING")
    session.add(models.RiskEvent(severity=models.RiskSeverity.critical, event_type="TRADING_DISABLED", source_component="operations-api", message=request.reason, is_kill_switch_triggered=True, metadata_json={"actor": actor.get("sub")}))
    return {"accepted": True, "kill_switch": state, "event": event}


@router.post("/admin/emergency/enable-trading")
async def emergency_enable_trading(request: ConfirmedActionRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_risk_write)]) -> dict:
    _require_confirmation(request.confirmation, "enable")
    await redis.client.delete("risk:kill_switch")
    event = await publish_runtime_command(session, redis, actor=actor, command_type="ENABLE_TRADING", payload={"reason": request.reason}, action="EMERGENCY_ENABLE_TRADING")
    session.add(models.RiskEvent(severity=models.RiskSeverity.high, event_type="TRADING_ENABLED", source_component="operations-api", message=request.reason, metadata_json={"actor": actor.get("sub")}))
    return {"accepted": True, "event": event}


@router.post("/admin/emergency/close-positions")
async def emergency_close_positions(request: ConfirmedActionRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_incident_response)]) -> dict:
    _require_confirmation(request.confirmation, "close")
    event = await publish_runtime_command(session, redis, actor=actor, command_type="CLOSE_POSITIONS", payload={"reason": request.reason, "reduce_only": True}, action="EMERGENCY_CLOSE_POSITIONS")
    session.add(models.RiskEvent(severity=models.RiskSeverity.critical, event_type="CLOSE_POSITIONS_REQUESTED", source_component="operations-api", message=request.reason, metadata_json={"actor": actor.get("sub")}))
    return {"accepted": True, "event": event}


@router.post("/admin/emergency/runtime-restart")
async def emergency_runtime_restart(request: ConfirmedActionRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_incident_response)]) -> dict:
    _require_confirmation(request.confirmation, "restart")
    event = await publish_runtime_command(session, redis, actor=actor, command_type="RUNTIME_RESTART", payload={"reason": request.reason}, action="EMERGENCY_RUNTIME_RESTART")
    session.add(models.RiskEvent(severity=models.RiskSeverity.high, event_type="RUNTIME_RESTART_REQUESTED", source_component="operations-api", message=request.reason, metadata_json={"actor": actor.get("sub")}))
    return {"accepted": True, "event": event}


@router.post("/admin/emergency/shutdown")
async def emergency_shutdown(request: ConfirmedActionRequest, redis: Annotated[RedisManager, Depends(get_redis)], session: Annotated[AsyncSession, Depends(get_session)], actor: Annotated[dict, Depends(require_incident_response)]) -> dict:
    _require_confirmation(request.confirmation, "shutdown")
    state = {"active": True, "reason": request.reason, "actor": actor.get("sub"), "activated_at": datetime.now(timezone.utc).isoformat(), "shutdown": True}
    await redis.client.set("risk:kill_switch", json.dumps(state, separators=(",", ":")))
    event = await publish_runtime_command(session, redis, actor=actor, command_type="EMERGENCY_SHUTDOWN", payload={"reason": request.reason, "kill_switch": state}, action="EMERGENCY_SHUTDOWN")
    session.add(models.RiskEvent(severity=models.RiskSeverity.critical, event_type="EMERGENCY_SHUTDOWN", source_component="operations-api", message=request.reason, is_kill_switch_triggered=True, is_circuit_breaker_triggered=True, metadata_json={"actor": actor.get("sub")}))
    return {"accepted": True, "kill_switch": state, "event": event}


@router.get("/operations/canary-limits")
async def operations_canary_limits(_: Annotated[dict, Depends(require_operations_access)], settings=Depends(get_settings)) -> dict:
    return {
        "max_canary_notional": settings.MAX_CANARY_NOTIONAL,
        "max_canary_position": settings.MAX_CANARY_POSITION,
        "trading_mode": settings.TRADING_MODE,
    }


@router.websocket("/ws/operations")
async def operations_stream(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    logger.info(
        "websocket_connection_attempt",
        extra={
            "path": websocket.url.path,
            "route_registered": True,
            "upgrade_header": websocket.headers.get("upgrade"),
            "connection_header": websocket.headers.get("connection"),
            "sec_websocket_key_present": websocket.headers.get("sec-websocket-key") is not None,
            "token_present": token is not None,
            "token_length": len(token or ""),
            "token_segments": len((token or "").split(".")) if token else 0,
            "token_prefix": (token or "")[:12],
            "token_suffix": (token or "")[-12:],
        },
    )
    if not token:
        logger.warning("websocket_auth_failed", extra={"path": websocket.url.path, "reason": "missing_token"})
        await websocket.close(code=1008)
        return
    try:
        actor = decode_token(token, get_settings())
        roles = set(actor.get("roles", []))
        permissions = set(actor.get("permissions", []))
        if not roles.intersection({"platform_admin", "risk_manager", "incident_responder", "read_only_analyst"}) and not permissions.intersection({"operations:read", "config:read", "risk:read"}):
            logger.warning("websocket_auth_failed", extra={"path": websocket.url.path, "reason": "insufficient_permissions", "token_length": len(token), "token_prefix": token[:12], "token_suffix": token[-12:], "token_segments": len(token.split("."))})
            await websocket.close(code=1008)
            return
        logger.info("websocket_auth_success", extra={"path": websocket.url.path, "token_length": len(token), "token_prefix": token[:12], "token_suffix": token[-12:], "token_segments": len(token.split(".")), "roles": list(roles), "permissions": list(permissions)})
    except HTTPException:
        logger.warning("websocket_auth_failed", extra={"path": websocket.url.path, "reason": "decode_failed", "token_length": len(token), "token_prefix": token[:12], "token_suffix": token[-12:], "token_segments": len(token.split("."))})
        await websocket.close(code=1008)
        return
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
    operations_actor: dict = {}
    engine_payload = (await operations_engines(redis, operations_actor))["engines"]
    await websocket.send_text(json.dumps({"type": "engine_health", "payload": {"engines": engine_payload}}, default=str))
    await websocket.send_text(json.dumps({"type": "exchange_connectivity", "payload": await operations_exchanges(redis, operations_actor)}, default=str))

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
        await websocket.send_text(json.dumps({"type": "reconciliation_completed", "payload": await operations_reconciliation(redis, operations_actor)}, default=str))
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

    positions = (await operations_positions(session, operations_actor))["items"]
    inventory = await operations_inventory(session, operations_actor)
    pnl = await operations_pnl(session, operations_actor)
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


def _audit_payload(row: models.AuditLog) -> dict:
    return {
        "id": str(row.id),
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "actor_service": row.actor_service,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id) if row.resource_id else None,
        "metadata": row.metadata_json,
        "before_state": row.before_state,
        "after_state": row.after_state,
        "occurred_at": _iso(row.occurred_at),
    }


def _runtime_event_payload(row: models.RuntimeEvent) -> dict:
    return {
        "id": str(row.id),
        "event_type": row.event_type,
        "source_component": row.source_component,
        "status": row.status,
        "command_id": row.command_id,
        "config_domain": row.config_domain,
        "config_version": row.config_version,
        "correlation_id": row.correlation_id,
        "payload": row.payload,
        "metadata": row.metadata_json,
        "acknowledged_at": _iso(row.acknowledged_at),
        "created_at": _iso(row.created_at),
    }


async def _exchange_accounts(session: AsyncSession) -> list[models.ExchangeAccount]:
    result = await session.execute(select(models.ExchangeAccount).order_by(models.ExchangeAccount.exchange_name, models.ExchangeAccount.account_alias))
    return list(result.scalars().all())


async def _exchange_account(session: AsyncSession, exchange_name: str, account_alias: str, environment: str) -> models.ExchangeAccount | None:
    result = await session.execute(select(models.ExchangeAccount).where(models.ExchangeAccount.exchange_name == exchange_name, models.ExchangeAccount.account_alias == account_alias, models.ExchangeAccount.environment == environment))
    return result.scalar_one_or_none()


def _aggregate_exchange_status(accounts: list[models.ExchangeAccount]) -> str:
    if not accounts:
        return "disconnected"
    if any(row.connection_status == "connected" for row in accounts):
        return "connected"
    if any(row.connection_status == "invalid_credentials" for row in accounts):
        return "invalid_credentials"
    if any(row.connection_status == "testing" for row in accounts):
        return "testing"
    if any(row.connection_status == "error" for row in accounts):
        return "error"
    return "disconnected"


async def _test_exchange_connection(row: models.ExchangeAccount, settings) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    definition = get_exchange_definition(row.exchange_name)
    result: dict[str, Any] = {
        "exchange_name": row.exchange_name,
        "account_alias": row.account_alias,
        "environment": row.environment,
        "status": "testing",
        "rest_status": "testing",
        "websocket_status": "testing",
        "private_ws_status": "not_supported",
        "last_tested_at": now.isoformat(),
        "error": None,
    }
    row.connection_status = "testing"
    row.last_tested_at = now
    rest = RestClient(definition, settings.HTTP_TIMEOUT_SECONDS)
    try:
        await rest.health()
        result["rest_status"] = "connected"
    except Exception as exc:
        result["rest_status"] = "error"
        result["status"] = "network_error"
        result["error"] = f"REST_FAILED:{exc.__class__.__name__}:{exc}"
        return result
    finally:
        await rest.close()

    try:
        async with websockets.connect(definition.websocket_url, ping_interval=settings.EXCHANGE_RECONNECT_MAX_DELAY_SECONDS, ping_timeout=settings.EXCHANGE_RECONNECT_MAX_DELAY_SECONDS):
            result["websocket_status"] = "connected"
    except Exception as exc:
        result["websocket_status"] = "error"
        result["status"] = "network_error"
        result["error"] = f"WEBSOCKET_FAILED:{exc.__class__.__name__}:{exc}"
        return result

    if row.exchange_name == "coinstore":
        cipher = SecretCipher(settings)
        api_key = cipher.decrypt(row.api_key_ciphertext)
        api_secret = cipher.decrypt(row.api_secret_ciphertext)
        if not api_key or not api_secret:
            result["private_ws_status"] = "invalid_credentials"
            result["status"] = "invalid_credentials"
            result["error"] = "INVALID_CREDENTIALS:missing_api_key_or_secret"
            return result
        credentials = ExecutionCredentials(api_key=api_key, api_secret=api_secret, passphrase=cipher.decrypt(row.passphrase_ciphertext))
        client = PrivateRestExecutionClient(ExecutionVenue.coinstore, settings, credentials)
        try:
            balances = await client.sync_balances()
            result["authenticated_rest_status"] = "connected"
            result["account_assets_seen"] = len(balances)
        except Exception as exc:
            result["private_ws_status"] = "not_tested"
            result["status"] = "invalid_credentials" if "signature" in str(exc).lower() or "401" in str(exc) else "rest_failed"
            result["error"] = f"AUTH_REST_FAILED:{exc.__class__.__name__}:{exc}"
            return result
        finally:
            await client.close()
        private_ws = CoinstorePrivateWebSocketClient(settings, credentials)
        try:
            private_result = await private_ws.test_connection()
            result["private_ws_status"] = "connected" if private_result.get("connected") else "error"
            result["private_ws_auth_response"] = private_result.get("auth_response")
            result["private_ws_subscribe_response"] = private_result.get("subscribe_response")
        except Exception as exc:
            result["private_ws_status"] = "error"
            result["status"] = "private_ws_failed"
            result["error"] = f"PRIVATE_WS_FAILED:{exc.__class__.__name__}:{exc}"
            return result

    result["status"] = "connected"
    return result


async def _apply_exchange_test_result(row: models.ExchangeAccount, result: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    row.last_tested_at = now
    row.rest_connected = result.get("rest_status") == "connected"
    row.websocket_connected = result.get("websocket_status") == "connected"
    row.private_ws_connected = result.get("private_ws_status") in {"connected", "not_supported"}
    row.connection_status = str(result.get("status") or "error")
    if row.connection_status == "connected":
        row.last_success_at = now
        row.last_error_message = None
    else:
        row.last_failure_at = now
        row.last_error_message = str(result.get("error") or row.connection_status)


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return f"{value[:2]}****{value[-2:]}"
    return f"{value[:4]}********{value[-4:]}"


def _exchange_account_payload(row: models.ExchangeAccount | None, settings=None) -> dict | None:
    if row is None:
        return None
    masked_api_key = None
    if settings is not None and row.api_key_ciphertext:
        try:
            masked_api_key = _mask_secret(SecretCipher(settings).decrypt(row.api_key_ciphertext))
        except Exception:
            masked_api_key = None
    return {
        "id": str(row.id),
        "exchange_name": row.exchange_name,
        "account_alias": row.account_alias,
        "environment": row.environment,
        "permissions": row.permissions,
        "is_enabled": row.is_enabled,
        "enabled": row.is_enabled,
        "api_key_masked": masked_api_key,
        "has_api_key": bool(row.api_key_ciphertext),
        "has_api_secret": bool(row.api_secret_ciphertext),
        "has_passphrase": bool(row.passphrase_ciphertext),
        "encryption_key_id": row.encryption_key_id,
        "rest_status": "connected" if row.rest_connected else "disconnected",
        "websocket_status": "connected" if row.websocket_connected else "disconnected",
        "private_ws_status": "connected" if row.private_ws_connected else "disconnected",
        "connection_status": row.connection_status,
        "last_tested_at": _iso(row.last_tested_at),
        "last_success_at": _iso(row.last_success_at),
        "last_failure_at": _iso(row.last_failure_at),
        "last_error_message": row.last_error_message,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def _volume_progress_payload(session: AsyncSession, redis: RedisManager, config_payload: dict) -> dict:
    now = datetime.now(timezone.utc)
    engine = VolumeEngine(DOMAIN_MODELS["volume"].model_validate(config_payload))
    since_hour = now.replace(minute=0, second=0, microsecond=0)
    since_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_week = since_day - timedelta(days=now.weekday())
    rows = (await session.execute(select(models.Trade.traded_at, models.Trade.price, models.Trade.quantity))).all()
    hourly = daily = weekly = 0.0
    for traded_at, price, quantity in rows:
        if traded_at is None:
            continue
        ts = traded_at if traded_at.tzinfo else traded_at.replace(tzinfo=timezone.utc)
        notional = _float(price) * _float(quantity)
        if ts >= since_hour:
            hourly += notional
        if ts >= since_day:
            daily += notional
        if ts >= since_week:
            weekly += notional
    data_health = _loads_json(await redis.client.get("engine:health:market-data-engine")) or {}
    runtime = data_health.get("runtime") or {}
    external_volume = 0.0
    for key in runtime.get("last_message_timestamp", {}) or {}:
        latest = _loads_json(await redis.client.get(f"latest:marketdata:ticker:{key}")) or {}
        external_volume += _float(latest.get("volume_24h")) * _float(latest.get("last_price") or latest.get("bid_price") or latest.get("ask_price") or 0)
    report = engine.progress(now=now, hourly_notional=hourly, daily_notional=daily, weekly_notional=weekly, external_market_volume_notional=external_volume)
    return {
        "settings": config_payload,
        "hourly": report.hourly.__dict__,
        "daily": report.daily.__dict__,
        "weekly": report.weekly.__dict__,
        "participation_rate": report.participation_rate,
        "pressure": report.pressure.__dict__,
        "external_market_volume_notional": external_volume,
    }


def _parse_balance_rows(raw: object) -> list[dict[str, float | str]]:
    payload = raw
    if isinstance(raw, dict):
        payload = raw.get("data") or raw.get("balances") or raw.get("result") or raw
    if isinstance(payload, dict):
        payload = payload.get("list") or payload.get("items") or payload.get("accounts") or []
    if not isinstance(payload, list):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="coinstore balance response format is unsupported")
    rows: list[dict[str, float | str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        asset = str(item.get("asset") or item.get("currency") or item.get("coin") or "").upper()
        if not asset:
            continue
        available = _float(item.get("available") or item.get("free") or item.get("normal") or 0)
        reserved = _float(item.get("reserved") or item.get("locked") or item.get("freeze") or 0)
        total = _float(item.get("total") or available + reserved)
        rows.append({"asset": asset, "available": available, "reserved": reserved, "total": total})
    return rows


def _require_confirmation(value: str, expected: str) -> None:
    if expected.lower() not in value.lower():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"confirmation must include '{expected}'")


def _require_config_domain_actor(domain: str, actor: dict) -> None:
    roles = set(actor.get("roles", []))
    permissions = set(actor.get("permissions", []))
    if "platform_admin" in roles:
        return
    if domain == "risk" and "risk_manager" not in roles and "risk:write" not in permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="risk config requires risk_manager or risk:write")
    if domain in {"strategy", "spread", "order_layers", "order_size", "volume", "liquidity", "inventory"} and not roles.intersection({"trader_operator", "risk_manager"}) and "config:write" not in permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="trading config requires trader_operator/risk_manager/config:write")


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

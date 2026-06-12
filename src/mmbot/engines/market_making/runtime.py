from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.core.config import RuntimeConfig, Settings, default_runtime_config
from mmbot.db import models
from mmbot.engines.inventory.engine import AssetBalance, InventoryEngine
from mmbot.engines.market_making.engine import InventoryState, MarketState, Quote, QuoteEngine
from mmbot.engines.risk.engine import OrderIntent as RiskOrderIntent, RiskEngine
from mmbot.engines.volume.engine import VolumeEngine, VolumeProgressReport
from mmbot.execution.coinstore import CoinstoreExecutionService
from mmbot.execution.models import ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent, TimeInForce
from mmbot.execution.paper import PaperExecutionEngine
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.production.canary import CanaryController, CanaryPolicy, CanaryState, LaunchMode
from mmbot.reconciliation.engine import ReconciliationEngine
from mmbot.redis.manager import EngineCommunicationLayer
from mmbot.runtime.events import publish_runtime_ack

logger = logging.getLogger(__name__)


class MarketMakerRuntime:
    def __init__(self, settings: Settings, session: AsyncSession, bus: EngineCommunicationLayer, quote_engine: QuoteEngine, metrics: RuntimeMetrics, runtime_config: RuntimeConfig | None = None):
        self.settings = settings
        self.session = session
        self.bus = bus
        self.quote_engine = quote_engine
        self.metrics = metrics
        self.runtime_config = runtime_config or default_runtime_config()
        self.inventory_engine = InventoryEngine(self.runtime_config.inventory)
        self.risk_engine = RiskEngine(self.runtime_config.risk)
        self.volume_engine = VolumeEngine(self.runtime_config.volume)
        self.reconciliation_engine = ReconciliationEngine()
        self.paper = PaperExecutionEngine(session, Decimal(str(settings.PAPER_STARTING_CASH)), settings.PAPER_BASE_ASSET, settings.PAPER_QUOTE_ASSET)
        self.coinstore: CoinstoreExecutionService | None = None
        self.mode = LaunchMode(settings.TRADING_MODE)
        self.canary = CanaryController(
            CanaryPolicy(
                max_position_notional=Decimal(str(min(self.runtime_config.risk.max_position_notional, settings.MAX_CANARY_POSITION))),
                max_daily_loss=Decimal(str(self.runtime_config.risk.max_daily_loss)),
                max_order_count=self.runtime_config.risk.max_open_orders,
                max_inventory_notional=Decimal(str(min(self.runtime_config.inventory.max_asset_exposure, settings.MAX_CANARY_POSITION))),
                max_order_notional=Decimal(str(min(self.runtime_config.risk.max_order_notional, settings.MAX_CANARY_NOTIONAL))),
            ),
            CanaryState(self.mode),
        )
        self.trading_enabled = self.runtime_config.strategy.trading_enabled
        self.quoting_enabled = self.runtime_config.strategy.quoting_enabled
        self.latest_ticker: dict[str, dict[str, Any]] = {}
        self.latest_orderbook: dict[str, OrderBookSnapshot] = {}
        self.latest_analytics: dict[str, dict[str, Any]] = {}
        self.latest_volume_progress: VolumeProgressReport | None = None
        self.last_reconciliation_at = datetime.now(timezone.utc)
        self.started = False
        self.pubsub: Any | None = None
        self.consumer_task: asyncio.Task[None] | None = None

    async def ensure_started(self) -> None:
        if self.started:
            return
        self.started = True
        self.pubsub = self.bus.pubsub.client.pubsub()
        patterns = [
            "marketdata:ticker:*",
            "marketdata:trades:*",
            "marketdata:orderbook:*",
            "marketdata:analytics:*",
            "runtime.config.*",
            "engine.commands.market-maker-engine",
        ]
        await self.pubsub.psubscribe(*patterns)
        self.consumer_task = asyncio.create_task(self._consume_bus_events(), name="market-maker-bus-consumer")
        logger.info("market_maker_runtime_started", extra={"mode": self.mode.value, "patterns": patterns})

    async def stop(self) -> None:
        if self.consumer_task is not None:
            self.consumer_task.cancel()
            await asyncio.gather(self.consumer_task, return_exceptions=True)
        if self.pubsub is not None:
            await self.pubsub.aclose()
        if self.coinstore is not None:
            await self.coinstore.close()

    async def tick(self) -> None:
        await self.ensure_started()
        if await self._kill_switch_active():
            self.metrics.increment("risk.kill_switch_blocks")
            return
        await self._load_latest_market_state()
        for symbol in self.settings.MARKET_DATA_SYMBOLS:
            await self._quote_symbol(symbol)
        await self._maybe_reconcile()

    async def ingest_market_event(self, channel: str, payload: dict[str, Any]) -> None:
        parts = channel.split(":")
        if len(parts) < 4:
            return
        kind, exchange, symbol = parts[1], parts[2], ":".join(parts[3:])
        key = f"{exchange}:{symbol}"
        if kind == "ticker":
            self.latest_ticker[key] = payload
        elif kind == "orderbook":
            self.latest_orderbook[key] = self._orderbook_from_payload(payload)
            fills = await self.paper.simulate_fills(symbol, self.latest_orderbook[key])
            self.metrics.increment("paper.fills", len(fills))
            self.metrics.increment("paper.pnl", float(self.paper.account.realized_pnl))
        elif kind == "analytics":
            self.latest_analytics[key] = payload

    def health(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "trading_enabled": self.trading_enabled,
            "quoting_enabled": self.quoting_enabled,
            "runtime_config": self.runtime_config.model_dump(),
            "volume_progress": self.latest_volume_progress,
            "known_tickers": len(self.latest_ticker),
            "known_orderbooks": len(self.latest_orderbook),
            "open_paper_orders": len(self.paper.open_orders),
            "paper_fills": len(self.paper.fills),
            "metrics": self.metrics.snapshot(),
        }

    async def _kill_switch_active(self) -> bool:
        try:
            state = await self.bus.cache.get_json("risk:kill_switch")
        except Exception as exc:
            reason = "kill_switch_state_unavailable"
            self.metrics.increment("risk.kill_switch_read_failures")
            if self.mode in {LaunchMode.paper, LaunchMode.shadow}:
                logger.warning("kill_switch_read_failed_non_live_mode", extra={"reason": reason, "error": str(exc), "mode": self.mode.value})
                return False
            self.canary.activate_shutdown(reason)
            logger.critical("kill_switch_read_failed", extra={"reason": reason, "error": str(exc), "mode": self.mode.value})
            return True
        if isinstance(state, dict) and state.get("active"):
            reason = str(state.get("reason") or "kill_switch_active")
            self.canary.activate_shutdown(reason)
            logger.critical("kill_switch_active", extra={"reason": reason, "mode": self.mode.value})
            return True
        return False

    async def _load_latest_market_state(self) -> None:
        for exchange in self.settings.MARKET_DATA_EXCHANGES:
            for symbol in self.settings.MARKET_DATA_SYMBOLS:
                for kind in ("ticker", "orderbook", "analytics"):
                    payload = await self._optional_cache_get_json(f"latest:marketdata:{kind}:{exchange}:{symbol}")
                    if isinstance(payload, dict):
                        await self.ingest_market_event(f"marketdata:{kind}:{exchange}:{symbol}", payload)

    async def _optional_cache_get_json(self, key: str) -> Any | None:
        try:
            return await self.bus.cache.get_json(key)
        except Exception as exc:
            self.metrics.increment("runtime.cache_read_misses")
            logger.debug("runtime_cache_read_ignored", extra={"key": key, "error": str(exc)})
            return None

    async def _consume_bus_events(self) -> None:
        if self.pubsub is None:
            return
        while True:
            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.1)
                    continue
                data = message.get("data")
                channel = message.get("channel")
                if isinstance(data, bytes):
                    data = data.decode()
                if isinstance(channel, bytes):
                    channel = channel.decode()
                if not isinstance(data, str) or not isinstance(channel, str):
                    continue
                payload = json.loads(data)
                if channel.startswith("marketdata:"):
                    await self.ingest_market_event(channel, payload)
                elif channel.startswith("runtime.config."):
                    command_id = payload.get("command_id") if isinstance(payload, dict) else None
                    await self._apply_config_payload(payload, command_id=str(command_id) if command_id else None)
                elif channel == "engine.commands.market-maker-engine":
                    await self._handle_command(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.increment("market_maker.bus_consumer_errors")
                logger.warning("market_maker_bus_consumer_recovered", extra={"error": str(exc)})
                await asyncio.sleep(1.0)

    async def _apply_config_payload(self, payload: dict[str, Any], command_id: str | None = None) -> None:
        runtime_payload = payload.get("runtime_config") or payload.get("payload", {}).get("runtime_config")
        if not isinstance(runtime_payload, dict):
            return
        runtime_config = RuntimeConfig.model_validate(runtime_payload)
        self.runtime_config = runtime_config
        self.quote_engine.update_settings(runtime_config.spread, runtime_config.order_size, runtime_config.inventory, runtime_config.order_layers)
        self.inventory_engine = InventoryEngine(runtime_config.inventory)
        self.risk_engine = RiskEngine(runtime_config.risk)
        self.volume_engine.update_settings(runtime_config.volume)
        self.trading_enabled = runtime_config.strategy.trading_enabled
        self.quoting_enabled = runtime_config.strategy.quoting_enabled
        self.metrics.increment("runtime.config_reloads")
        await publish_runtime_ack(self.session, self.bus, component="market-maker-engine", command_id=command_id or str(payload.get("command_id") or ""), event_type="runtime_config_reload_ack", status="acknowledged", payload={"domains": list(runtime_payload.keys())})
        logger.info("runtime_config_reloaded", extra={"domains": list(runtime_payload.keys())})

    async def _handle_command(self, message: dict[str, Any]) -> None:
        command_id = str(message.get("command_id") or "")
        command = str(message.get("command_type") or "").upper()
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        if command == "CONFIG_RELOAD":
            await self._apply_config_payload(payload, command_id=command_id)
        elif command in {"DISABLE_TRADING", "EMERGENCY_SHUTDOWN"}:
            self.trading_enabled = False
            self.quoting_enabled = False
            self.risk_engine.activate_kill_switch(str(payload.get("reason") or command.lower()))
            await self._cancel_all_orders()
        elif command == "ENABLE_TRADING":
            if not await self._kill_switch_active():
                self.trading_enabled = True
                self.quoting_enabled = True
        elif command == "CANCEL_ALL_ORDERS":
            await self._cancel_all_orders()
        elif command == "CLOSE_POSITIONS":
            await self._close_paper_positions()
        elif command == "RUNTIME_RESTART":
            await self._cancel_all_orders()
            self.latest_ticker.clear()
            self.latest_orderbook.clear()
            self.latest_analytics.clear()
        elif command == "STRATEGY_COMMAND":
            action = str(payload.get("action") or "").lower()
            if action in {"pause", "stop"}:
                self.trading_enabled = False
                self.quoting_enabled = False
                if action == "stop":
                    await self._cancel_all_orders()
            elif action in {"start", "resume"} and not await self._kill_switch_active():
                self.trading_enabled = True
                self.quoting_enabled = True
        self.metrics.increment(f"runtime.commands.{command.lower() or 'unknown'}")
        await publish_runtime_ack(self.session, self.bus, component="market-maker-engine", command_id=command_id or None, event_type="runtime_command_ack", status="acknowledged", payload={"command_type": command})

    async def _quote_symbol(self, symbol: str) -> None:
        if not self.trading_enabled or not self.quoting_enabled or not self.runtime_config.strategy.quoting_enabled:
            self.metrics.increment("market_maker.strategy_blocks")
            return
        orderbook_key = next((key for key in self.latest_orderbook if key.endswith(f":{symbol}")), None)
        if orderbook_key is None:
            return
        orderbook = self.latest_orderbook[orderbook_key]
        if not orderbook.bids or not orderbook.asks:
            return
        bid = max(level.price for level in orderbook.bids)
        ask = min(level.price for level in orderbook.asks)
        mid = (bid + ask) / 2
        analytics = self.latest_analytics.get(orderbook_key, {})
        spread_data = analytics.get("spread", {}) if isinstance(analytics, dict) else {}
        liquidity_data = analytics.get("liquidity", {}) if isinstance(analytics, dict) else {}
        volatility = float(analytics.get("realized_volatility", 0.0)) if isinstance(analytics, dict) else 0.0
        imbalance = float(liquidity_data.get("imbalance_ratio", 0.0)) if isinstance(liquidity_data, dict) else 0.0
        balances = self._asset_balances(mid)
        inventory_report = self.inventory_engine.report(balances, self.settings.PAPER_BASE_ASSET)
        inventory_state = InventoryState(
            base_ratio=next((exposure.ratio for exposure in inventory_report.exposures if exposure.asset == self.settings.PAPER_BASE_ASSET), 0.0),
            target_base_ratio=self.inventory_engine.settings.target_base_ratio,
            exposure_notional=float(self.paper.exposure_notional()),
        )
        market_state = MarketState(symbol=symbol, mid_price=mid, spread_bps=float(spread_data.get("spread_bps", 0.0)), volatility=volatility, liquidity_imbalance=imbalance)
        volume_report = await self._volume_progress(symbol, mid)
        adjusted_market_state = MarketState(
            symbol=symbol,
            mid_price=mid,
            spread_bps=max(1.0, market_state.spread_bps * volume_report.pressure.spread_multiplier),
            volatility=market_state.volatility,
            liquidity_imbalance=market_state.liquidity_imbalance,
        )
        quotes = self.quote_engine.generate_quotes(adjusted_market_state, inventory_state)
        quotes = self._apply_volume_pressure(quotes, volume_report, bid, ask)
        self.metrics.increment("market_maker.quote_refreshes")
        self.metrics.increment("market_maker.quotes_generated", len(quotes))
        logger.info("quote_generated", extra={"symbol": symbol, "quote_count": len(quotes), "mid_price": mid, "volume_pressure": volume_report.pressure.reason, "volume_urgency": volume_report.pressure.urgency})
        await self._submit_quotes(symbol, quotes, orderbook)
        await self._persist_inventory(symbol, mid, balances)

    async def _submit_quotes(self, symbol: str, quotes: list[Quote], orderbook: OrderBookSnapshot) -> None:
        if not self.trading_enabled:
            self.metrics.increment("market_maker.trading_disabled_blocks")
            return
        for quote in quotes:
            side = ExecutionSide.buy if quote.side == "buy" else ExecutionSide.sell
            venue = ExecutionVenue.coinstore if self.mode in {LaunchMode.canary, LaunchMode.live} else ExecutionVenue.binance
            intent = OrderIntent(
                venue=venue,
                symbol=symbol,
                side=side,
                order_type=ExecutionOrderType.limit,
                quantity=Decimal(str(quote.quantity)),
                price=Decimal(str(quote.price)),
                client_order_id=quote.client_order_id,
                time_in_force=TimeInForce.gtc,
            )
            risk_intent = RiskOrderIntent(symbol, quote.side, quote.price, quote.quantity)
            try:
                self.risk_engine.assert_order_allowed(
                    risk_intent,
                    position_notional=float(self.paper.exposure_notional()),
                    total_exposure=float(self.paper.exposure_notional()),
                    open_orders=len(self.paper.open_orders),
                    daily_pnl=float(self.paper.account.realized_pnl),
                )
                self.metrics.increment("risk.approvals")
                logger.info("risk_approved", extra={"symbol": symbol, "side": quote.side, "price": quote.price, "quantity": quote.quantity})
                decision = self.canary.evaluate(intent)
                if not decision.accepted:
                    self.metrics.increment("market_maker.quote_rejections")
                    continue
                if self.mode is LaunchMode.paper:
                    await self.paper.place_order(intent, orderbook)
                    self.metrics.increment("paper.orders_created")
                elif decision.execution_allowed:
                    await self._place_live_order(intent)
                else:
                    self.metrics.increment("market_maker.quote_rejections")
            except Exception as exc:
                self.metrics.increment("risk.rejections")
                self.metrics.increment("market_maker.quote_rejections")
                await self._persist_risk_event(symbol, str(exc))

    async def _persist_risk_event(self, symbol: str, message: str) -> None:
        self.session.add(models.RiskEvent(severity=models.RiskSeverity.high, event_type="PAPER_ORDER_REJECTED", source_component="market-maker-engine", message=message, metadata_json={"symbol": symbol, "mode": self.mode.value}))
        await self.session.flush()

    async def _persist_inventory(self, symbol: str, mid: float, balances: list[AssetBalance]) -> None:
        account_id = await self.paper._ensure_exchange_account()
        for balance in balances:
            self.session.add(models.InventorySnapshot(exchange_account_id=account_id, asset=balance.asset, total_balance=Decimal(str(balance.total)), available_balance=Decimal(str(balance.available)), reserved_balance=Decimal(str(balance.reserved)), valuation_asset=self.settings.PAPER_QUOTE_ASSET, valuation_price=Decimal(str(balance.price)), valuation_amount=Decimal(str(balance.total * balance.price)), captured_at=datetime.now(timezone.utc), metadata_json={"symbol": symbol, "mode": "paper"}))
        await self.session.flush()
        logger.info("db_insert_success", extra={"table": "inventory_snapshots", "symbol": symbol, "asset_count": len(balances)})

    async def _volume_progress(self, symbol: str, mid: float) -> VolumeProgressReport:
        now = datetime.now(timezone.utc)
        since_hour = now.replace(minute=0, second=0, microsecond=0)
        since_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since_week = since_day - timedelta(days=now.weekday())
        rows = (await self.session.execute(select(models.Trade.traded_at, models.Trade.price, models.Trade.quantity))).all()
        hourly = daily = weekly = 0.0
        for traded_at, price, quantity in rows:
            if traded_at is None:
                continue
            ts = traded_at if traded_at.tzinfo else traded_at.replace(tzinfo=timezone.utc)
            notional = float(price or 0) * float(quantity or 0)
            if ts >= since_hour:
                hourly += notional
            if ts >= since_day:
                daily += notional
            if ts >= since_week:
                weekly += notional
        ticker_key = next((key for key in self.latest_ticker if key.endswith(f":{symbol}")), None)
        ticker = self.latest_ticker.get(ticker_key or "", {})
        external_volume = float(ticker.get("volume_24h") or ticker.get("volume") or 0.0) * mid
        report = self.volume_engine.progress(now=now, hourly_notional=hourly, daily_notional=daily, weekly_notional=weekly, external_market_volume_notional=external_volume)
        self.latest_volume_progress = report
        self.metrics.set_gauge("volume.hourly_progress_ratio", report.hourly.progress_ratio)
        self.metrics.set_gauge("volume.daily_progress_ratio", report.daily.progress_ratio)
        self.metrics.set_gauge("volume.weekly_progress_ratio", report.weekly.progress_ratio)
        self.metrics.set_gauge("volume.participation_rate", report.participation_rate)
        self.metrics.set_gauge("volume.execution_urgency", report.pressure.urgency)
        return report

    def _apply_volume_pressure(self, quotes: list[Quote], report: VolumeProgressReport, best_bid: float, best_ask: float) -> list[Quote]:
        signal = report.pressure
        if not signal.enabled or signal.size_multiplier == 1.0:
            return quotes
        now = datetime.now(timezone.utc)
        if not self.volume_engine.can_apply_pressure_order(now):
            return quotes
        max_size = self.runtime_config.order_size.max_order_size
        adjusted: list[Quote] = []
        for quote in quotes:
            quantity = min(max_size, quote.quantity * signal.size_multiplier)
            if quote.side == "buy":
                price = min(quote.price, best_ask * 0.999999)
            else:
                price = max(quote.price, best_bid * 1.000001)
            adjusted.append(Quote(quote.side, round(price, 8), quantity, quote.level, quote.client_order_id))
        return adjusted

    async def _place_live_order(self, intent: OrderIntent) -> None:
        service = await self._coinstore()
        order = await service.place_order(intent)
        self.metrics.increment("coinstore.orders_created")
        logger.info("coinstore_order_submitted", extra={"symbol": intent.symbol, "client_order_id": order.client_order_id, "exchange_order_id": order.exchange_order_id, "status": order.status.value})

    async def _cancel_all_orders(self) -> None:
        for client_order_id in list(self.paper.open_orders):
            await self.paper.cancel_order(client_order_id)
        if self.mode in {LaunchMode.canary, LaunchMode.live}:
            service = await self._coinstore()
            for symbol in self.settings.MARKET_DATA_SYMBOLS:
                cancelled = await service.cancel_all_orders(symbol)
                self.metrics.increment("coinstore.orders_cancelled", len(cancelled))
        self.metrics.increment("runtime.cancel_all_orders")

    async def _coinstore(self) -> CoinstoreExecutionService:
        if self.coinstore is None:
            self.coinstore = CoinstoreExecutionService(self.settings, self.session)
        return self.coinstore

    async def _close_paper_positions(self) -> None:
        if not self.latest_orderbook:
            return
        base_quantity = self.paper.account.balances.get(self.settings.PAPER_BASE_ASSET, Decimal("0"))
        if base_quantity == 0:
            return
        symbol = f"{self.settings.PAPER_BASE_ASSET}/{self.settings.PAPER_QUOTE_ASSET}"
        orderbook = next(iter(self.latest_orderbook.values()))
        best_bid = max(level.price for level in orderbook.bids) if orderbook.bids else None
        best_ask = min(level.price for level in orderbook.asks) if orderbook.asks else None
        if base_quantity > 0 and best_bid is not None:
            side = ExecutionSide.sell
            price = Decimal(str(best_bid))
            quantity = base_quantity
        elif base_quantity < 0 and best_ask is not None:
            side = ExecutionSide.buy
            price = Decimal(str(best_ask))
            quantity = abs(base_quantity)
        else:
            return
        intent = OrderIntent(ExecutionVenue.binance, symbol, side, ExecutionOrderType.limit, quantity, price, f"emergency-close-{uuid.uuid4().hex[:12]}", TimeInForce.ioc)
        await self.paper.place_order(intent, orderbook)
        self.metrics.increment("runtime.close_positions")

    async def _maybe_reconcile(self) -> None:
        now = datetime.now(timezone.utc)
        if (now - self.last_reconciliation_at).total_seconds() < self.settings.RECONCILIATION_INTERVAL_SECONDS:
            return
        self.last_reconciliation_at = now
        snapshot = self.paper.reconciliation_snapshot()
        mismatches = self.reconciliation_engine.reconcile(snapshot, snapshot)
        self.metrics.increment("reconciliation.runs")
        self.metrics.increment("reconciliation.mismatches", len(mismatches))
        logger.info("reconciliation_completed", extra={"mismatch_count": len(mismatches), "mode": self.mode.value})
        if mismatches:
            self._handle_reconciliation_mismatches(len(mismatches))

    def _handle_reconciliation_mismatches(self, mismatch_count: int) -> None:
        self.metrics.increment("reconciliation.alerts", mismatch_count)
        self.canary.activate_shutdown("reconciliation_mismatch")

    def _asset_balances(self, mid: float) -> list[AssetBalance]:
        base_total = float(self.paper.account.balances.get(self.settings.PAPER_BASE_ASSET, Decimal("0")))
        quote_total = float(self.paper.account.balances.get(self.settings.PAPER_QUOTE_ASSET, Decimal("0")))
        return [
            AssetBalance(self.settings.PAPER_BASE_ASSET, base_total, base_total, 0.0, mid),
            AssetBalance(self.settings.PAPER_QUOTE_ASSET, quote_total, quote_total, 0.0, 1.0),
        ]

    def _orderbook_from_payload(self, payload: dict[str, Any]) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            exchange=str(payload["exchange"]),
            symbol=str(payload["symbol"]),
            bids=[OrderBookLevel(float(item["price"]), float(item["size"])) for item in payload.get("bids", [])],
            asks=[OrderBookLevel(float(item["price"]), float(item["size"])) for item in payload.get("asks", [])],
            source_timestamp=datetime.fromisoformat(payload["source_timestamp"]),
            sequence=payload.get("sequence"),
        )

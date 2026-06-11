from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mmbot.core.config import Settings, default_runtime_config
from mmbot.db import models
from mmbot.engines.inventory.engine import AssetBalance, InventoryEngine
from mmbot.engines.market_making.engine import InventoryState, MarketState, Quote, QuoteEngine
from mmbot.engines.risk.engine import OrderIntent as RiskOrderIntent, RiskEngine
from mmbot.execution.models import ExecutionOrderType, ExecutionSide, ExecutionVenue, OrderIntent, TimeInForce
from mmbot.execution.paper import PaperExecutionEngine
from mmbot.exchanges.types import OrderBookLevel, OrderBookSnapshot
from mmbot.observability.metrics import RuntimeMetrics
from mmbot.production.canary import CanaryController, CanaryPolicy, CanaryState, LaunchMode
from mmbot.reconciliation.engine import ReconciliationEngine
from mmbot.redis.manager import EngineCommunicationLayer

logger = logging.getLogger(__name__)


class MarketMakerRuntime:
    def __init__(self, settings: Settings, session: AsyncSession, bus: EngineCommunicationLayer, quote_engine: QuoteEngine, metrics: RuntimeMetrics):
        self.settings = settings
        self.session = session
        self.bus = bus
        self.quote_engine = quote_engine
        self.metrics = metrics
        config = default_runtime_config()
        self.inventory_engine = InventoryEngine(config.inventory)
        self.risk_engine = RiskEngine(config.risk)
        self.reconciliation_engine = ReconciliationEngine()
        self.paper = PaperExecutionEngine(session, Decimal(str(settings.PAPER_STARTING_CASH)), settings.PAPER_BASE_ASSET, settings.PAPER_QUOTE_ASSET)
        self.mode = LaunchMode(settings.TRADING_MODE)
        self.canary = CanaryController(
            CanaryPolicy(
                max_position_notional=Decimal(str(min(config.risk.max_position_notional, settings.MAX_CANARY_POSITION))),
                max_daily_loss=Decimal(str(config.risk.max_daily_loss)),
                max_order_count=config.risk.max_open_orders,
                max_inventory_notional=Decimal(str(min(config.inventory.max_asset_exposure, settings.MAX_CANARY_POSITION))),
                max_order_notional=Decimal(str(min(config.risk.max_order_notional, settings.MAX_CANARY_NOTIONAL))),
            ),
            CanaryState(self.mode),
        )
        self.latest_ticker: dict[str, dict[str, Any]] = {}
        self.latest_orderbook: dict[str, OrderBookSnapshot] = {}
        self.latest_analytics: dict[str, dict[str, Any]] = {}
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
        ]
        await self.pubsub.psubscribe(*patterns)
        self.consumer_task = asyncio.create_task(self._consume_market_data(), name="market-maker-marketdata-consumer")
        logger.info("market_maker_runtime_started", extra={"mode": self.mode.value, "patterns": patterns})

    async def stop(self) -> None:
        if self.consumer_task is not None:
            self.consumer_task.cancel()
            await asyncio.gather(self.consumer_task, return_exceptions=True)
        if self.pubsub is not None:
            await self.pubsub.aclose()

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
            "known_tickers": len(self.latest_ticker),
            "known_orderbooks": len(self.latest_orderbook),
            "open_paper_orders": len(self.paper.open_orders),
            "paper_fills": len(self.paper.fills),
            "metrics": self.metrics.snapshot(),
        }

    async def _kill_switch_active(self) -> bool:
        state = await self.bus.cache.get_json("risk:kill_switch")
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
                    payload = await self.bus.cache.get_json(f"latest:marketdata:{kind}:{exchange}:{symbol}")
                    if isinstance(payload, dict):
                        await self.ingest_market_event(f"marketdata:{kind}:{exchange}:{symbol}", payload)

    async def _consume_market_data(self) -> None:
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
                await self.ingest_market_event(channel, json.loads(data))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.increment("market_maker.marketdata_consumer_errors")
                logger.warning("market_maker_marketdata_consumer_recovered", extra={"error": str(exc)})
                await asyncio.sleep(1.0)

    async def _quote_symbol(self, symbol: str) -> None:
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
        quotes = self.quote_engine.generate_quotes(market_state, inventory_state)
        self.metrics.increment("market_maker.quote_refreshes")
        self.metrics.increment("market_maker.quotes_generated", len(quotes))
        logger.info("quote_generated", extra={"symbol": symbol, "quote_count": len(quotes), "mid_price": mid})
        await self._submit_quotes(symbol, quotes, orderbook)
        await self._persist_inventory(symbol, mid, balances)

    async def _submit_quotes(self, symbol: str, quotes: list[Quote], orderbook: OrderBookSnapshot) -> None:
        for quote in quotes:
            side = ExecutionSide.buy if quote.side == "buy" else ExecutionSide.sell
            intent = OrderIntent(
                venue=ExecutionVenue.binance,
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
                if self.mode in {LaunchMode.paper, LaunchMode.shadow}:
                    if self.mode is LaunchMode.paper:
                        await self.paper.place_order(intent, orderbook)
                        self.metrics.increment("paper.orders_created")
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

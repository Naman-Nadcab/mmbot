from decimal import Decimal

from mmbot.reconciliation.engine import BalanceRecord, OrderRecord, PnlRecord, ReconciliationEngine, ReconciliationSeverity, ReconciliationSnapshot


def test_reconciliation_detects_balance_order_and_pnl_mismatches():
    exchange = ReconciliationSnapshot(
        balances=[BalanceRecord('USDT', Decimal('100'), Decimal('90'), Decimal('10'))],
        orders=[OrderRecord('cid', 'exid', 'BTC/USDT', 'open', Decimal('0'), Decimal('1'))],
        pnl=PnlRecord(Decimal('10'), Decimal('1'), Decimal('0.1')),
    )
    internal = ReconciliationSnapshot(
        balances=[BalanceRecord('USDT', Decimal('99'), Decimal('90'), Decimal('9'))],
        orders=[OrderRecord('cid', 'exid', 'BTC/USDT', 'filled', Decimal('1'), Decimal('0'))],
        pnl=PnlRecord(Decimal('9'), Decimal('1'), Decimal('0.1')),
    )
    mismatches = ReconciliationEngine().reconcile(exchange, internal)
    categories = {mismatch.category for mismatch in mismatches}
    assert {'balance', 'order', 'pnl'} <= categories
    assert any(mismatch.severity is ReconciliationSeverity.critical for mismatch in mismatches)
    alerts = ReconciliationEngine().generate_alerts(mismatches)
    assert alerts

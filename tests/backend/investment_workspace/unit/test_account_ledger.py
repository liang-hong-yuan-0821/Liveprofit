# test-catalog-begin
# {
#   "purpose": "投资工作区 / account_ledger（账户、账本）",
#   "keywords": [
#     "投资工作区",
#     "现金",
#     "费用",
#     "流程",
#     "历史审计",
#     "账户账本",
#     "订单",
#     "投资组合",
#     "重放",
#     "account_ledger",
#     "cash",
#     "fee",
#     "flow",
#     "history",
#     "ledger",
#     "order",
#     "portfolio",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.modules.investment_workspace.application.account_ledger import (
    LedgerBaseline, LedgerMovement, replay_account_balance,
)


T0 = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)
PORTFOLIO_ID = uuid4()


def _event(kind, cash, holdings=(), *, day=1, price=None, fee="0", replaces=None, reason=None):
    return LedgerMovement(
        portfolio_id=PORTFOLIO_ID, event_id=uuid4(), recorded_at=T0 + timedelta(days=day, hours=1),
        effective_at=T0 + timedelta(days=day), kind=kind,
        cash_delta=Decimal(cash), holdings_delta=holdings,
        fill_price=None if price is None else Decimal(price), fee=Decimal(fee),
        supersedes_id=replaces, reason=reason,
    )


def _baseline():
    return LedgerBaseline(PORTFOLIO_ID, T0, Decimal("1000"), (("CN", "000001.SZ", Decimal("10")),))


def test_replay_keeps_external_flow_separate_from_trade_and_corporate_action():
    flow = _event("CASH_FLOW", "500")
    buy = _event("TRADE", "-101", (("CN", "000001.SZ", Decimal("10")),),
                 day=2, price="10", fee="1")
    dividend = _event("CORPORATE_ACTION", "5", (("CN", "000001.SZ", Decimal("2")),), day=3)
    balance = replay_account_balance(_baseline(), (flow, buy, dividend), as_of=T0 + timedelta(days=4))
    assert balance.cash == Decimal("1404")
    assert balance.holdings == (("CN", "000001.SZ", Decimal("22")),)
    assert balance.external_flow_total == Decimal("500")
    assert balance.effective_event_ids == (flow.event_id, buy.event_id, dividend.event_id)
    assert balance.external_flow_event_ids == (flow.event_id,)


def test_correction_replays_original_before_recording_and_replacement_after():
    original = _event("TRADE", "-101", (("CN", "000001.SZ", Decimal("10")),),
                      price="10", fee="1")
    replacement = _event("TRADE", "-91", (("CN", "000001.SZ", Decimal("9")),),
                         day=2, price="10", fee="1", replaces=original.event_id,
                         reason="券商成交数量更正")
    early = replay_account_balance(_baseline(), (original, replacement),
                                   as_of=T0 + timedelta(days=1, hours=2))
    late = replay_account_balance(_baseline(), (original, replacement),
                                  as_of=T0 + timedelta(days=3))
    assert early.cash == Decimal("899") and early.holdings[0][2] == 20
    assert late.cash == Decimal("909") and late.holdings[0][2] == 19
    assert late.effective_event_ids == (replacement.event_id,)


def test_replay_rejects_forked_corrections_and_impossible_balance():
    original = _event("TRADE", "-100", (("CN", "000001.SZ", Decimal("10")),), price="10")
    first = _event("TRADE", "-90", (("CN", "000001.SZ", Decimal("9")),),
                   day=2, price="10", replaces=original.event_id, reason="修订一")
    second = _event("TRADE", "-80", (("CN", "000001.SZ", Decimal("8")),),
                    day=3, price="10", replaces=original.event_id, reason="修订二")
    with pytest.raises(ValueError, match="forked"):
        replay_account_balance(_baseline(), (original, first, second), as_of=T0 + timedelta(days=4))
    oversell = _event("TRADE", "200", (("CN", "000001.SZ", Decimal("-20")),), price="10")
    with pytest.raises(ValueError, match="holding is negative"):
        replay_account_balance(_baseline(), (oversell,), as_of=T0 + timedelta(days=2))


def test_future_or_unrecorded_movement_never_changes_as_of_balance():
    future = _event("CASH_FLOW", "500", day=2)
    balance = replay_account_balance(_baseline(), (future,), as_of=T0 + timedelta(days=1))
    assert balance.cash == Decimal("1000")
    assert balance.external_flow_total == 0
    assert balance.effective_event_ids == ()


def test_replay_is_independent_of_input_order_and_accepts_cash_dividend():
    original = _event("CASH_FLOW", "100")
    replacement = _event("CASH_FLOW", "80", day=2, replaces=original.event_id,
                         reason="入金记录更正")
    dividend = _event("CORPORATE_ACTION", "5", day=3)
    expected = replay_account_balance(_baseline(), (original, replacement, dividend),
                                      as_of=T0 + timedelta(days=4))
    reversed_result = replay_account_balance(_baseline(), (dividend, replacement, original),
                                             as_of=T0 + timedelta(days=4))
    assert reversed_result == expected
    assert expected.cash == Decimal("1085")
    assert expected.external_flow_total == Decimal("80")
    assert expected.external_flow_event_ids == (replacement.event_id,)


def test_cross_portfolio_and_zero_change_events_are_rejected():
    foreign = _event("CASH_FLOW", "1")
    foreign = LedgerMovement(**{**foreign.__dict__, "portfolio_id": uuid4()})
    with pytest.raises(ValueError, match="portfolio mismatch"):
        replay_account_balance(_baseline(), (foreign,), as_of=T0 + timedelta(days=2))
    zero_action = _event("CORPORATE_ACTION", "0",
                         (("CN", "000001.SZ", Decimal(0)),))
    with pytest.raises(ValueError, match="invalid corporate action"):
        replay_account_balance(_baseline(), (zero_action,), as_of=T0 + timedelta(days=2))


def test_trade_void_retains_history_and_removes_fee_and_holdings_from_current_replay():
    trade = _event("TRADE", "-101", (("CN", "000001.SZ", Decimal("10")),),
                   price="10", fee="1")
    void = _event("VOID", "0", day=2, replaces=trade.event_id, reason="成交报告撤销")
    void = LedgerMovement(**{**void.__dict__, "effective_at": trade.effective_at})
    before = replay_account_balance(_baseline(), (trade, void),
                                    as_of=T0 + timedelta(days=1, hours=2))
    after = replay_account_balance(_baseline(), (trade, void),
                                   as_of=T0 + timedelta(days=3))
    assert before.cash == Decimal("899") and before.holdings[0][2] == 20
    assert after.cash == Decimal("1000") and after.holdings[0][2] == 10
    assert after.effective_event_ids == (void.event_id,)
    assert after.external_flow_total == 0
    invalid_time = LedgerMovement(**{**void.__dict__, "effective_at": T0 + timedelta(days=2)})
    with pytest.raises(ValueError, match="valid predecessor"):
        replay_account_balance(_baseline(), (trade, invalid_time), as_of=T0 + timedelta(days=3))
    invalid_cash = LedgerMovement(**{**void.__dict__, "cash_delta": Decimal("1")})
    with pytest.raises(ValueError, match="without a balance change"):
        replay_account_balance(_baseline(), (trade, invalid_cash), as_of=T0 + timedelta(days=3))


def test_historical_effective_balance_can_use_later_recorded_correction_or_void():
    trade = _event("TRADE", "-101", (("CN", "000001.SZ", Decimal("10")),),
                   price="10", fee="1")
    correction = _event("TRADE", "-91", (("CN", "000001.SZ", Decimal("9")),),
                        day=3, price="10", fee="1", replaces=trade.event_id,
                        reason="迟到成交更正")
    correction = LedgerMovement(**{**correction.__dict__, "effective_at": trade.effective_at})
    historical_at = T0 + timedelta(days=1, hours=2)
    known_then = replay_account_balance(_baseline(), (trade, correction),
                                        as_of=historical_at)
    known_now = replay_account_balance(_baseline(), (trade, correction),
                                       as_of=historical_at,
                                       recorded_as_of=T0 + timedelta(days=4))
    assert known_then.cash == Decimal("899") and known_then.holdings[0][2] == 20
    assert known_now.cash == Decimal("909") and known_now.holdings[0][2] == 19
    assert known_now.effective_event_ids == (correction.event_id,)

    void = _event("VOID", "0", day=3, replaces=trade.event_id, reason="迟到撤销")
    void = LedgerMovement(**{**void.__dict__, "effective_at": trade.effective_at})
    voided_now = replay_account_balance(_baseline(), (trade, void),
                                        as_of=historical_at,
                                        recorded_as_of=T0 + timedelta(days=4))
    assert voided_now.cash == Decimal("1000") and voided_now.holdings[0][2] == 10
    assert voided_now.effective_event_ids == (void.event_id,)
    with pytest.raises(ValueError, match="recorded_as_of must be timezone aware"):
        replay_account_balance(_baseline(), (trade,), as_of=historical_at,
                               recorded_as_of=datetime(2026, 9, 30))


def test_correction_moving_effective_date_removes_false_original_at_old_date():
    original = _event("CASH_FLOW", "100", day=1)
    corrected = _event("CASH_FLOW", "100", day=3,
                       replaces=original.event_id, reason="原流水日期错误")
    corrected = LedgerMovement(**{**corrected.__dict__,
                                  "recorded_at": T0 + timedelta(days=4)})
    old_day = T0 + timedelta(days=1, hours=2)
    known_then = replay_account_balance(_baseline(), (original, corrected),
                                        as_of=old_day)
    known_now = replay_account_balance(_baseline(), (original, corrected),
                                       as_of=old_day,
                                       recorded_as_of=T0 + timedelta(days=5))
    new_day_known_now = replay_account_balance(_baseline(), (original, corrected),
                                               as_of=T0 + timedelta(days=3, hours=2),
                                               recorded_as_of=T0 + timedelta(days=5))
    assert known_then.cash == Decimal("1100")
    assert known_now.cash == Decimal("1000")
    assert new_day_known_now.cash == Decimal("1100")

# test-catalog-begin
# {
#   "purpose": "量化研究 / stock_candidate_builder：Local candidate conversion remains diagnostic until independent evidence is certified.",
#   "keywords": [
#     "量化研究",
#     "K线",
#     "市场分析",
#     "来源证据",
#     "状态",
#     "个股分析",
#     "stock_candidate_builder",
#     "bars",
#     "market",
#     "source",
#     "status",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/dataset_builder.py",
#     "backend/modules/quant_research/application/stock_candidate_builder.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Local candidate conversion remains diagnostic until independent evidence is certified."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset
from backend.modules.quant_research.application.stock_candidate_builder import (
    StockAccountFacts, build_stock_candidates,
)


def _snapshot():
    days = tuple(day.date() for day in pd.bdate_range("2025-01-02", periods=251))
    code = "000001.SZ"
    tables = {
        "instrument": pd.DataFrame([{
            "ts_code": code, "instrument_type": "stock", "list_date": days[0], "delist_date": None,
        }]),
        "daily": pd.DataFrame([{
            "ts_code": code, "trade_date": day, "close": 10, "amount": 30000,
        } for day in days]),
        "adj_factor": pd.DataFrame([{
            "ts_code": code, "trade_date": day, "adj_factor": 1 if index == 0 else 2,
        } for index, day in enumerate(days)]),
        "trade_status": pd.DataFrame([{
            "ts_code": code, "trade_date": days[-1], "is_st": False,
            "is_suspended": False, "market_board": "main", "source": "tushare",
        }]),
    }
    quality = DatasetQuality(
        status="READY", issues=(), as_of=days[-1], source_rows={},
        certification_issues=("corporate_actions: historical adjustment evidence absent",),
        instrument_types=("stock",),
    )
    return ResearchDataset(tables, quality), days


def test_adjustment_amount_and_account_facts_are_converted_without_certification():
    snapshot, days = _snapshot()
    result = build_stock_candidates(
        snapshot, as_of=days[-1], trading_days=days,
        account_facts={"000001.SZ": StockAccountFacts(held=True, cooldown_until=days[-1])},
    )
    assert result.gaps == {}
    assert result.universe.candidate_set_matches_snapshot
    assert result.universe.evidence_issues
    candidate, = result.candidates
    assert candidate.adjusted_bars[0] == (days[0], Decimal(5))
    assert candidate.adjusted_bars[-1] == (days[-1], Decimal(10))
    assert candidate.listed_sessions == 251
    assert candidate.adv20_cny == Decimal(30000000)
    assert candidate.held and candidate.cooldown_until == days[-1]


@pytest.mark.parametrize("change,expected", [
    ("factor", "as-of price or adjustment absent"),
    ("amount", "ADV20 amount incomplete"),
    ("status", "as-of stock status invalid"),
    ("account", "account facts absent"),
])
def test_missing_market_or_account_fact_omits_candidate_and_reports_code(change, expected):
    snapshot, days = _snapshot()
    if change == "factor":
        snapshot.tables["adj_factor"].drop(snapshot.tables["adj_factor"].index[-1], inplace=True)
    elif change == "amount":
        snapshot.tables["daily"].loc[snapshot.tables["daily"].index[-1], "amount"] = None
    elif change == "status":
        snapshot.tables["trade_status"]["is_st"] = snapshot.tables["trade_status"]["is_st"].astype(object)
        snapshot.tables["trade_status"].loc[0, "is_st"] = None
    result = build_stock_candidates(
        snapshot, as_of=days[-1], trading_days=days,
        account_facts={} if change == "account" else {"000001.SZ": StockAccountFacts(False)},
    )
    assert result.candidates == ()
    assert expected in result.gaps["000001.SZ"]
    assert result.universe.missing_codes == ("000001.SZ",)


def test_future_fact_and_wrong_snapshot_date_fail_closed():
    snapshot, days = _snapshot()
    with pytest.raises(ValueError, match="candidate date differs"):
        build_stock_candidates(snapshot, as_of=days[-2], trading_days=days,
                               account_facts={"000001.SZ": StockAccountFacts(False)})
    snapshot.tables["daily"].loc[len(days)] = {
        "ts_code": "000001.SZ", "trade_date": date(2027, 1, 1), "close": 10, "amount": 30000,
    }
    result = build_stock_candidates(snapshot, as_of=days[-1], trading_days=days,
                                    account_facts={"000001.SZ": StockAccountFacts(False)})
    assert result.candidates == ()
    assert "future market fact" in result.gaps["000001.SZ"]


def test_prelisting_bars_cannot_satisfy_250_sessions_and_nat_delist_is_open():
    snapshot, days = _snapshot()
    snapshot.tables["instrument"].loc[0, "list_date"] = days[-1]
    snapshot.tables["instrument"].loc[0, "delist_date"] = pd.NaT
    result = build_stock_candidates(snapshot, as_of=days[-1], trading_days=days,
                                    account_facts={"000001.SZ": StockAccountFacts(False)})
    assert result.candidates == ()
    assert "fewer than 250 evidenced bars" in result.gaps["000001.SZ"]


def test_untrusted_status_source_cannot_become_candidate():
    snapshot, days = _snapshot()
    snapshot.tables["trade_status"].loc[0, "source"] = "unknown"
    result = build_stock_candidates(snapshot, as_of=days[-1], trading_days=days,
                                    account_facts={"000001.SZ": StockAccountFacts(False)})
    assert result.candidates == ()
    assert "as-of stock status invalid" in result.gaps["000001.SZ"]


def test_missing_market_board_cannot_become_candidate():
    snapshot, days = _snapshot()
    snapshot.tables["trade_status"].loc[0, "market_board"] = None
    result = build_stock_candidates(snapshot, as_of=days[-1], trading_days=days,
                                    account_facts={"000001.SZ": StockAccountFacts(False)})
    assert result.candidates == ()
    assert "as-of stock status invalid" in result.gaps["000001.SZ"]


def test_recent_adjustment_gap_cannot_hide_behind_250_older_stock_bars():
    snapshot, days = _snapshot()
    snapshot.tables["adj_factor"].drop(index=245, inplace=True)
    result = build_stock_candidates(snapshot, as_of=days[-1], trading_days=days,
                                    account_facts={"000001.SZ": StockAccountFacts(False)})
    assert result.candidates == ()
    assert "recent adjusted-price window incomplete" in result.gaps["000001.SZ"]

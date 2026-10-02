# test-catalog-begin
# {
#   "purpose": "量化研究 / fund_candidate_builder",
#   "keywords": [
#     "量化研究",
#     "K线",
#     "ETF",
#     "历史审计",
#     "停牌",
#     "fund_candidate_builder",
#     "bars",
#     "etf",
#     "history",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/dataset_builder.py",
#     "backend/modules/quant_research/application/fund_candidate_builder.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset
from backend.modules.quant_research.application.fund_candidate_builder import (
    FundClassificationFacts, build_fund_candidates,
)


def _snapshot():
    days = tuple(value.date() for value in pd.bdate_range("2025-01-02", periods=251))
    code = "510300.SH"
    tables = {
        "instrument": pd.DataFrame([{"ts_code": code, "instrument_type": "fund",
                                     "list_date": days[0], "delist_date": None}]),
        "daily": pd.DataFrame([{"ts_code": code, "trade_date": day, "close": 10.,
                                "amount": 30000.} for day in days]),
        "adj_factor": pd.DataFrame([{"ts_code": code, "trade_date": day,
                                     "adj_factor": 1. if index == 0 else 2.}
                                    for index, day in enumerate(days)]),
        "factor": pd.DataFrame([{"ts_code": code, "trade_date": days[-1],
                                 "atr_bfq": 1.25}]),
        "etf_catalog": pd.DataFrame([{"ts_code": code, "index_code": "000300.SH",
                                      "etf_type": "股票型", "list_date": days[0],
                                      "list_status": "L",
                                      "available_at": "2025-01-03T16:00:00+08:00"}]),
    }
    quality = DatasetQuality(status="READY", issues=(), as_of=days[-1], source_rows={},
                             instrument_types=("fund",))
    return ResearchDataset(tables, quality), days


def _build(dataset, days, facts=None):
    if facts is None:
        facts = {"510300.SH": FundClassificationFacts("000300.SH", "EQUITY_300", False)}
    return build_fund_candidates(dataset, as_of=days[-1], trading_days=days,
                                 classification_facts=facts)


def test_adjusted_etf_price_raw_atr_and_amount_are_distinct_inputs():
    dataset, days = _snapshot()
    result = _build(dataset, days)
    assert result.gaps == {} and result.excluded_codes == ()
    candidate, = result.candidates
    assert candidate.adjusted_bars[0] == (days[0], Decimal(5))
    assert candidate.adjusted_bars[-1] == (days[-1], Decimal(10))
    assert candidate.atr20_raw == Decimal("1.25")
    assert candidate.adv20_cny == Decimal(30000000)
    assert candidate.listed_sessions == 251
    assert candidate.eligible(decision_date=days[-1], lookback=180)
    assert "etf_rules: historical trading rules not verified" in result.certification_issues


def test_missing_classification_or_atr_is_reported_without_candidate():
    dataset, days = _snapshot()
    missing = _build(dataset, days, {})
    assert missing.candidates == ()
    assert "ETF category or suspension fact absent" in missing.gaps["510300.SH"]
    dataset.tables["factor"].loc[0, "atr_bfq"] = None
    no_atr = _build(dataset, days)
    assert no_atr.candidates == ()
    assert "as-of ETF adjustment or ATR invalid" in no_atr.gaps["510300.SH"]


def test_future_catalog_and_qdii_cannot_become_trial_candidate():
    dataset, days = _snapshot()
    dataset.tables["etf_catalog"].loc[0, "available_at"] = (days[-1] + timedelta(days=1)).isoformat()
    future = _build(dataset, days)
    assert future.candidates == ()
    assert "ETF catalog identity or availability invalid" in future.gaps["510300.SH"]
    dataset.tables["etf_catalog"].loc[0, "available_at"] = "2025-01-03T16:00:00+08:00"
    dataset.tables["etf_catalog"].loc[0, "etf_type"] = "QDII"
    qdii = _build(dataset, days)
    assert qdii.candidates == ()
    assert "ETF catalog identity or availability invalid" in qdii.gaps["510300.SH"]


def test_catalog_availability_requires_explicit_timezone():
    dataset, days = _snapshot()
    dataset.tables["etf_catalog"].loc[0, "available_at"] = "2025-01-03 16:00:00"
    result = _build(dataset, days)
    assert result.candidates == ()
    assert "ETF catalog identity or availability invalid" in result.gaps["510300.SH"]


def test_paused_catalog_listing_cannot_become_tradable_candidate():
    dataset, days = _snapshot()
    dataset.tables["etf_catalog"].loc[0, "list_status"] = "P"
    result = _build(dataset, days)
    assert result.candidates == ()
    assert "ETF catalog identity or availability invalid" in result.gaps["510300.SH"]


def test_other_category_is_explicitly_excluded_and_suspension_is_not_defaulted():
    dataset, days = _snapshot()
    other = _build(dataset, days, {"510300.SH": FundClassificationFacts("000300.SH", "OTHER", False)})
    assert other.candidates == () and other.gaps == {}
    assert other.excluded_codes == ("510300.SH",)
    suspended = _build(dataset, days, {"510300.SH": FundClassificationFacts("000300.SH", "EQUITY_300", True)})
    assert suspended.candidates[0].suspended
    assert not suspended.candidates[0].eligible(decision_date=days[-1], lookback=180)
    with pytest.raises(ValueError, match="classification and suspension"):
        FundClassificationFacts("000300.SH", "EQUITY_300", None)


def test_missing_latest_amount_and_prelisting_history_do_not_create_candidate():
    dataset, days = _snapshot()
    dataset.tables["daily"].loc[250, "amount"] = None
    no_amount = _build(dataset, days)
    assert "ETF ADV20 amount incomplete" in no_amount.gaps["510300.SH"]
    dataset.tables["daily"].loc[250, "amount"] = 30000.
    dataset.tables["instrument"].loc[0, "list_date"] = days[-1]
    dataset.tables["etf_catalog"].loc[0, "list_date"] = days[-1]
    prelisting = _build(dataset, days)
    assert prelisting.candidates == ()
    assert "fewer than 250 evidenced ETF bars" in prelisting.gaps["510300.SH"]


def test_recent_adjustment_gap_cannot_hide_behind_250_older_etf_bars():
    dataset, days = _snapshot()
    dataset.tables["adj_factor"].drop(index=245, inplace=True)
    result = _build(dataset, days)
    assert result.candidates == ()
    assert "ETF recent adjusted-price window incomplete" in result.gaps["510300.SH"]

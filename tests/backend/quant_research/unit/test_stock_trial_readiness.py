# test-catalog-begin
# {
#   "purpose": "量化研究 / stock_trial_readiness",
#   "keywords": [
#     "量化研究",
#     "认证证书",
#     "策略族",
#     "公共组件",
#     "个股分析",
#     "stock_trial_readiness",
#     "certificate",
#     "family",
#     "shared",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/dataset_builder.py",
#     "backend/modules/quant_research/application/stock_candidate_builder.py",
#     "backend/modules/quant_research/application/stock_trial_readiness.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset
from backend.modules.quant_research.application.stock_candidate_builder import StockAccountFacts
from backend.modules.quant_research.application.stock_trial_readiness import inspect_stock_trial_inputs


def _snapshot():
    days = tuple(value.date() for value in pd.bdate_range("2025-01-02", periods=251))
    code = "000001.SZ"
    tables = {
        "instrument": pd.DataFrame([{"ts_code": code, "instrument_type": "stock",
                                     "list_date": days[0], "delist_date": None}]),
        "daily": pd.DataFrame([{"ts_code": code, "trade_date": day, "close": 10.,
                                "amount": 30000.} for day in days]),
        "adj_factor": pd.DataFrame([{"ts_code": code, "trade_date": day,
                                     "adj_factor": 1.} for day in days]),
        "trade_status": pd.DataFrame([{"ts_code": code, "trade_date": days[-1],
                                       "is_st": False, "is_suspended": False,
                                       "market_board": "MAIN", "source": "tushare"}]),
        "benchmark_daily": pd.DataFrame([{"ts_code": "000300.SH", "trade_date": day,
                                          "close": 4000., "source": "tushare"} for day in days]),
    }
    quality = DatasetQuality(status="READY", issues=(), as_of=days[-1], source_rows={},
                             instrument_types=("stock",))
    return ResearchDataset(tables, quality), days


def _inspect(trial_id, dataset, days):
    return inspect_stock_trial_inputs(
        trial_id, dataset, as_of=days[-1], trading_days=days,
        account_facts={"000001.SZ": StockAccountFacts(False)},
    )


def test_local_complete_input_still_cannot_claim_target_certification():
    dataset, days = _snapshot()
    result = _inspect("stock_medium_momentum:60:10:200", dataset, days)
    assert result.local_complete
    assert result.stocks.universe.candidate_set_matches_snapshot
    assert result.benchmark.complete_local and result.benchmark.required_sessions == 200
    assert len(result.stocks.candidates) == 1
    assert len(result.definition_hash) == 64
    assert "benchmark_daily: source availability not verified" in result.certification_issues
    assert "account: locked ownership and cooldown provenance not verified" in result.certification_issues
    assert "stock_market_facts: historical source availability not verified" in result.certification_issues
    assert "coverage: independent calendar absent" in result.certification_issues
    assert not hasattr(result, "intent")


def test_stock_and_benchmark_gaps_are_reported_together_without_target():
    dataset, days = _snapshot()
    dataset.tables["daily"].loc[250, "amount"] = None
    dataset.tables["benchmark_daily"].drop(index=250, inplace=True)
    result = _inspect("stock_short_reversion:2:1.5:3", dataset, days)
    assert not result.local_complete
    assert "ADV20 amount incomplete" in result.stocks.gaps["000001.SZ"]
    assert result.stocks.universe.missing_codes == ("000001.SZ",)
    assert "stock: 1 candidate input gaps" in result.local_issues
    assert "benchmark_daily: required window incomplete" in result.local_issues
    assert result.benchmark.missing_days == (days[-1],)
    assert result.benchmark.bars == ()


def test_registered_family_sets_benchmark_window_and_rejects_wrong_family():
    dataset, days = _snapshot()
    medium = _inspect("stock_medium_momentum:60:10:60", dataset, days)
    short = _inspect("stock_short_reversion:2:1.5:3", dataset, days)
    assert medium.benchmark.required_sessions == 60
    assert short.benchmark.required_sessions == 120
    with pytest.raises(ValueError, match="stock portfolio trial required"):
        _inspect("etf_dual_momentum:60:1:WEEKLY", dataset, days)
    with pytest.raises(ValueError, match="preregistered"):
        _inspect("stock_medium_momentum:60:10:999", dataset, days)


def test_snapshot_date_mismatch_is_rejected_by_shared_converters():
    dataset, days = _snapshot()
    with pytest.raises(ValueError, match="date differs"):
        inspect_stock_trial_inputs(
            "stock_short_reversion:2:1.5:3", dataset,
            as_of=date(2026, 1, 1), trading_days=days,
            account_facts={"000001.SZ": StockAccountFacts(False)},
        )


def test_local_quality_issue_is_not_misclassified_as_independent_certificate():
    dataset, days = _snapshot()
    dataset = ResearchDataset(dataset.tables, DatasetQuality(
        status="INSUFFICIENT_EVIDENCE", issues=("daily: local row invalid",),
        as_of=days[-1], source_rows={}, instrument_types=("stock",),
    ))
    result = _inspect("stock_short_reversion:2:1.5:3", dataset, days)
    assert "daily: local row invalid" in result.local_issues
    assert "daily: local row invalid" not in result.certification_issues
    assert "dataset: not READY" in result.local_issues
    assert "dataset: not READY" not in result.certification_issues

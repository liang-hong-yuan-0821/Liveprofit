"""Shared fixtures/builders for tests.backend.quant_research.unit.test_fund_representative_readiness; no test cases."""

from datetime import timedelta
import pandas as pd
import pytest
from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset
from backend.modules.quant_research.application.fund_candidate_builder import FundClassificationFacts
from backend.modules.quant_research.application.fund_representative_readiness import (
    inspect_monthly_fund_representatives,
)


def _snapshot():
    days = tuple(value.date() for value in pd.bdate_range("2025-01-02", periods=251))
    securities = (
        ("510300.SH", "000300.SH", "EQUITY_300", 30000.),
        ("510310.SH", "000300.SH", "EQUITY_300", 40000.),
        ("511010.SH", "000012.SH", "BOND_MEDIUM", 35000.),
        ("510999.SH", "999999.SH", "OTHER", 10000.),
    )
    tables = {
        "instrument": pd.DataFrame([{"ts_code": code, "instrument_type": "fund",
                                     "list_date": days[0], "delist_date": None}
                                    for code, _, _, _ in securities]),
        "daily": pd.DataFrame([{"ts_code": code, "trade_date": day, "close": 10.,
                                "amount": amount}
                               for code, _, _, amount in securities for day in days]),
        "adj_factor": pd.DataFrame([{"ts_code": code, "trade_date": day,
                                     "adj_factor": 1.}
                                    for code, _, _, _ in securities for day in days]),
        "factor": pd.DataFrame([{"ts_code": code, "trade_date": days[-1],
                                 "atr_bfq": 1.}
                                for code, _, _, _ in securities]),
        "etf_catalog": pd.DataFrame([{"ts_code": code, "index_code": index,
                                      "etf_type": "债券型" if category == "BOND_MEDIUM" else "股票型",
                                      "list_date": days[0], "list_status": "L",
                                      "available_at": "2025-01-03T16:00:00+08:00"}
                                     for code, index, category, _ in securities]),
    }
    facts = {code: FundClassificationFacts(index, category, False)
             for code, index, category, _ in securities}
    quality = DatasetQuality(status="READY", issues=(), as_of=days[-1], source_rows={},
                             instrument_types=("fund",))
    return ResearchDataset(tables, quality), days, facts


def _inspect(trial_id, dataset, days, facts, *, month_end=True):
    return inspect_monthly_fund_representatives(
        trial_id, dataset, as_of=days[-1], decision_date=days[-1] + timedelta(days=1),
        trading_days=days, classification_facts=facts,
        is_month_end_selection=month_end,
    )

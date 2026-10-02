# test-catalog-begin
# {
#   "purpose": "数据采集 / audit_history：The inventory uses an independent calendar and a read-only database transaction.",
#   "keywords": [
#     "数据采集",
#     "交易日历",
#     "重复请求",
#     "复权因子",
#     "历史审计",
#     "来源证据",
#     "个股分析",
#     "audit_history",
#     "calendar",
#     "duplicate",
#     "factor",
#     "history",
#     "source",
#     "stock"
#   ],
#   "covers": [
#     "db/instrument/audit_history.py",
#     "db/instrument/ingest/stock_factors.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""The inventory uses an independent calendar and a read-only database transaction."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.audit_history import (
    _STOCK_SQL,
    _year_days,
    compare_audits,
    run_audit,
)
from db.instrument.ingest.stock_factors import REQUIRED_QFQ


class Calendar:
    def __init__(self, dates):
        self.dates = dates

    def get_trade_cal(self, start, end):
        return pd.DataFrame({"trade_date": pd.to_datetime(self.dates),
                             "is_open": [1] * len(self.dates)})


def test_audit_uses_calendar_and_never_writes_source():
    conn = MagicMock()
    conn.execute.return_value.fetchall.side_effect = [
        [("stock", 2, 0, 0), ("fund", 1, 1, 0)],
        [(date(2026, 9, 24), "000001.SZ")],
        [(date(2026, 9, 23), 596)],
        [(date(2026, 9, 24), "000001.SZ", None, True)],
        [],
        [],
    ]
    conn.execute.return_value.fetchone.side_effect = [
        (0, None, None),
        (2, 0, 1, 0, 0, 2, 2, 2, 0, 2, 1, 0, 1),
        (0,),
        (1, 1, 0, 0),
    ]
    report = run_audit(
        conn, Calendar(["2026-09-23", "2026-09-24"]),
        start=2026, end=2026, through=date(2026, 9, 24),
        crosscheck_calendar=False,
    )
    assert conn.execute.call_args_list[0].args == ("SET TRANSACTION READ ONLY",)
    assert report["years"][0]["unexplained_bar"] == 1
    assert report["years"][0]["status_row_absent_with_bar"] == 1
    assert report["years"][0]["status_row_absent_unexplained_no_bar"] == 1
    assert report["years"][0]["broad_fund"]["daily_gap"] == 1
    assert report["years"][0]["unexplained_bar_examples"] == [
        {"trade_date": "2026-09-24", "ts_code": "000001.SZ"},
    ]
    assert report["years"][0]["suspension_bar_spikes"] == [
        {"trade_date": "2026-09-23", "suspended_with_bar": 596},
    ]
    assert report["years"][0]["suspension_scope_conflicts"] == [
        {"trade_date": "2026-09-24", "ts_code": "000001.SZ",
         "scope": None, "has_bar": True},
    ]
    assert report["years"][0]["source_suspension_conflicts"] == []
    conn.commit.assert_not_called()


def test_historical_factor_audit_tracks_every_required_stock_indicator():
    assert all(f"{column} IS NULL" in _STOCK_SQL for column in REQUIRED_QFQ)


def test_calendar_partial_or_duplicate_response_fails_closed():
    with pytest.raises(RuntimeError, match="suspiciously short"):
        _year_days(Calendar(["2025-01-02"]), 2025, date(2026, 9, 24))
    with pytest.raises(RuntimeError, match="duplicate"):
        _year_days(Calendar(["2026-09-24", "2026-09-24"]), 2026,
                   date(2026, 9, 24), crosscheck_calendar=False)


def test_audit_comparison_accepts_repairs_but_flags_new_gaps_and_catalog_drift():
    prior = {"through": "2026-09-24", "years": [{
        "year": 2026, "calendar_sha256": "same", "expected": 10,
        "unexplained_bar": 3, "invalid_bar": 0, "adj_gap": 0,
        "factor_gap_including_warmup": 4, "status_gap": 4,
        "broad_fund": {"listed_fund_days": 5, "daily_gap": 1,
                       "adj_gap": 0, "factor_gap_including_warmup": 3},
    }]}
    current = {"through": prior["through"], "years": [{
        **prior["years"][0], "unexplained_bar": 1, "status_gap": 2,
        "broad_fund": {**prior["years"][0]["broad_fund"], "daily_gap": 0},
    }]}
    assert compare_audits(current, prior) == []
    current["years"][0]["adj_gap"] = 1
    current["years"][0]["calendar_sha256"] = "changed"
    current["years"][0]["expected"] = 11
    findings = compare_audits(current, prior)
    assert any("calendar changed" in item for item in findings)
    assert any("stock expected population changed" in item for item in findings)
    assert any("stock adj_gap increased" in item for item in findings)
    assert compare_audits({"through": prior["through"], "years": []}, prior) == [
        "2026: current year absent",
    ]
    with pytest.raises(ValueError, match="same through date"):
        compare_audits({**current, "through": "2026-09-25"}, prior)

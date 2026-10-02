# test-catalog-begin
# {
#   "purpose": "数据采集 / factor_day_backfill：Read/write contract tests for bounded historical factor-day ingestion (no live DB).",
#   "keywords": [
#     "数据采集",
#     "K线",
#     "每日",
#     "复权因子",
#     "历史审计",
#     "来源证据",
#     "factor_day_backfill",
#     "bars",
#     "daily",
#     "factor",
#     "history",
#     "source"
#   ],
#   "covers": [
#     "db/instrument/ingest/factor_day_backfill.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Read/write contract tests for bounded historical factor-day ingestion (no live DB)."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest import factor_day_backfill as fb


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def _frame(*codes):
    data = {column: [1.0] * len(codes) for column in fb.REQUIRED}
    data.update(ts_code=list(codes), trade_date=["2016-01-04"] * len(codes),
                close=[1.0] * len(codes))
    return pd.DataFrame(data)


def test_factor_day_writes_only_local_missing_codes(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value = _Rows([("000001.SZ", date(1991, 4, 3), 1.0)])
    provider = MagicMock()
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame",
                        lambda *_: _frame("000001.SZ", "999999.SZ"))
    upsert = MagicMock()
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", upsert)

    result = fb.backfill_factor_day(conn, provider, date(2016, 1, 4))

    assert result == {"day": "2016-01-04", "written": 1,
                      "warmup": 0, "unresolved": []}
    written = upsert.call_args.args[1]
    assert written["ts_code"].tolist() == ["000001.SZ"]
    assert conn.commit.call_count == 2


def test_factor_day_keeps_missing_source_symbol_unresolved(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value = _Rows([
        ("000001.SZ", date(1991, 4, 3), 1.0),
        ("000002.SZ", date(1991, 1, 29), 1.0),
    ])
    provider = MagicMock()
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame",
                        lambda *_: _frame("000001.SZ"))
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", MagicMock())

    result = fb.backfill_factor_day(conn, provider, date(2016, 1, 4))

    assert result["written"] == 1
    assert result["unresolved"] == ["000002.SZ"]


def test_absent_ipo_first_day_factor_is_proven_short_history_without_fake_row(monkeypatch):
    conn = MagicMock()
    conn.execute.side_effect = [
        _Rows([("000001.SZ", date(1991, 4, 3), 1.0),
               ("000002.SZ", date(2016, 1, 4), 1.0)]),
        _Rows([(date(2016, 1, 4),)]),
    ]
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame",
                        lambda *_: _frame("000001.SZ"))
    upsert = MagicMock()
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", upsert)
    result = fb.backfill_factor_day(conn, MagicMock(), date(2016, 1, 4))
    assert result == {"day": "2016-01-04", "written": 1,
                      "warmup": 1, "unresolved": []}
    assert upsert.call_args.args[1].ts_code.tolist() == ["000001.SZ"]


@pytest.mark.parametrize("price", [1.01, None, float("inf"), -1.0, "bad"])
def test_direct_factor_price_must_match_daily_before_write(monkeypatch, price):
    conn = MagicMock()
    conn.execute.return_value = _Rows([("000001.SZ", date(1991, 4, 3), 1.0)])
    frame = _frame("000001.SZ")
    frame["close"] = price
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame", lambda *_: frame)
    upsert = MagicMock()
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", upsert)
    with pytest.raises(ValueError, match="FACTOR_DAY_CLOSE_MISMATCH"):
        fb.backfill_factor_day(conn, MagicMock(), date(2016, 1, 4))
    upsert.assert_not_called()


def test_factor_day_rejects_wrong_source_date_before_write(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value = _Rows([("000001.SZ", date(1991, 4, 3), 1.0)])
    provider = MagicMock()
    frame = _frame("000001.SZ")
    frame["trade_date"] = "2016-01-05"
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame", lambda *_: frame)
    upsert = MagicMock()
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", upsert)

    try:
        fb.backfill_factor_day(conn, provider, date(2016, 1, 4))
    except ValueError as exc:
        assert str(exc) == "FACTOR_DAY_INVALID_KEYS"
    else:
        raise AssertionError("wrong trade date must fail")
    upsert.assert_not_called()


def test_one_day_window_is_valid(monkeypatch, tmp_path):
    monkeypatch.setattr(fb, "FAILURE_LIST", tmp_path / "failures.json")
    seen = []

    def record(_conn, _provider, day, _aliases):
        seen.append(day)
        return {"day": day.isoformat(), "written": 1, "warmup": 0,
                "unresolved": []}

    monkeypatch.setattr(fb, "backfill_factor_day", record)
    result = fb._run_stock_factor_day_backfill(
        MagicMock(), MagicMock, date(2016, 1, 4), date(2016, 1, 4),
    )

    assert seen == [date(2016, 1, 4)]
    assert result["days"] == result["written"] == 1


def test_partial_days_do_not_trigger_source_outage_break(monkeypatch, tmp_path):
    monkeypatch.setattr(fb, "FAILURE_LIST", tmp_path / "failures.json")

    def partial(_conn, _provider, day, _aliases):
        return {"day": day.isoformat(), "written": 100,
                "warmup": 0, "unresolved": ["IPO_WITH_UNKNOWN_PRIOR_HISTORY"]}

    monkeypatch.setattr(fb, "backfill_factor_day", partial)
    result = fb._run_stock_factor_day_backfill(
        MagicMock(), MagicMock, date(2016, 1, 4), date(2016, 1, 11),
    )

    assert result["days"] == 6
    assert len(result["failed_days"]) == 6


def test_warmup_requires_bars_before_audit_window():
    conn = MagicMock()
    conn.execute.return_value = _Rows([(date(2016, 1, 4),)])

    assert fb._verified_warmup(
        conn, "001369.SZ", date(2016, 1, 4), date(2016, 1, 4),
    ) is True
    assert fb._verified_warmup(
        conn, "001979.SZ", date(2015, 12, 30), date(2016, 1, 4),
    ) is False


def test_warmup_accepts_only_source_verified_full_day_halt():
    conn = MagicMock()
    conn.execute.return_value = _Rows([
        (date(2016, 6, 6),), (date(2016, 6, 7),), (date(2016, 6, 8),),
        (date(2016, 6, 13),), (date(2016, 6, 14),), (date(2016, 6, 15),),
        (date(2016, 6, 16),), (date(2016, 6, 17),), (date(2016, 6, 20),),
        (date(2016, 6, 21),), (date(2016, 6, 22),), (date(2016, 6, 23),),
        (date(2016, 6, 24),), (date(2016, 6, 27),), (date(2016, 6, 28),),
        (date(2016, 6, 29),), (date(2016, 7, 1),),
    ])
    source = MagicMock()
    source._quant_verified_halt_cache = None
    source.get_verified_full_day_suspensions_df.return_value = pd.DataFrame({
        "ts_code": ["601611.SH"], "trade_date": ["2016-06-30"],
    })
    assert fb._verified_warmup(
        conn, "601611.SH", date(2016, 6, 6), date(2016, 7, 1), source,
    ) is True
    source.get_verified_full_day_suspensions_df.assert_called_once_with("2016-06-30")
    source._quant_verified_halt_cache.clear()
    source.get_verified_full_day_suspensions_df.return_value = pd.DataFrame(
        columns=["ts_code", "trade_date"])
    assert fb._verified_warmup(
        conn, "601611.SH", date(2016, 6, 6), date(2016, 7, 1), source,
    ) is False


def test_bse_alias_requires_same_raw_close(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value = _Rows([("920680.BJ", date(2021, 11, 15), 14.83)])
    provider = MagicMock()
    frame = _frame("839680.BJ")
    frame["trade_date"] = "2025-01-02"
    frame["close"] = 14.83
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame", lambda *_: frame)
    upsert = MagicMock()
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", upsert)

    result = fb.backfill_factor_day(
        conn, provider, date(2025, 1, 2), {"839680.BJ": "920680.BJ"},
    )
    assert result["written"] == 1
    assert upsert.call_args.args[1]["ts_code"].tolist() == ["920680.BJ"]

    frame["close"] = 14.82
    try:
        fb.backfill_factor_day(
            conn, provider, date(2025, 1, 2), {"839680.BJ": "920680.BJ"},
        )
    except ValueError as exc:
        assert str(exc) == "FACTOR_DAY_ALIAS_CLOSE_MISMATCH"
    else:
        raise AssertionError("alias with different raw close must fail")


def test_bse_direct_factor_row_wins_during_dual_code_publication(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value = _Rows([("920017.BJ", date(2021, 11, 15), 22.08)])
    frame = _frame("430017.BJ", "920017.BJ")
    frame["trade_date"] = "2025-07-25"
    frame["close"] = 22.08
    frame.loc[0, "atr_qfq"] = 1.1535
    frame.loc[1, "atr_qfq"] = 1.15924
    monkeypatch.setattr(fb, "fetch_stock_technical_factor_frame", lambda *_: frame)
    upsert = MagicMock()
    monkeypatch.setattr(fb, "bulk_upsert_factor_daily", upsert)

    result = fb.backfill_factor_day(
        conn, MagicMock(), date(2025, 7, 25), {"430017.BJ": "920017.BJ"},
    )

    assert result["written"] == 1
    written = upsert.call_args.args[1]
    assert written["source_code"].tolist() == ["920017.BJ"]
    assert written["atr_qfq"].tolist() == [1.15924]

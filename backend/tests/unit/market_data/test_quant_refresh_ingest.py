from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.modules.market_data.application.refresh_policy import Resource
from db.instrument.ingest import refresh, stock_factors
from db.instrument.ingest.frames import (
    StoreFetchError,
    fetch_stock_technical_factor_frame,
)
from db.instrument.ingest.stock_factors import REQUIRED_QFQ

DAY = "2026-09-23"


def _quant_codes(size=20):
    return [f"{number:06d}.SZ" for number in range(size)]


def _factor_frame(codes, *, warmup=()):
    rows = []
    for code in codes:
        row = {"ts_code": code, "trade_date": DAY}
        row.update({column: (None if code in warmup else 10.0) for column in REQUIRED_QFQ})
        rows.append(row)
    return pd.DataFrame(rows)


def _status_frame(codes):
    return pd.DataFrame([{
        "ts_code": code, "trade_date": DAY, "is_suspended": False,
        "is_st": False, "market_board": "主板", "up_limit": 11.0,
        "down_limit": 9.0,
    } for code in codes])


def test_internal_refresh_spec_preserves_component_gaps_and_execution_groups():
    code = _quant_codes(1)[0]
    spec = refresh.RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, DAY, (code,),
        component_missing_units=({"component": "qfq", "code": code,
                                  "trade_date": DAY, "reason": "MISSING_ROW"},),
        component_thresholds={"daily": 1.0, "qfq": 0.95,
                              "adj_factor": 0.95, "trade_status": 0.95},
        operation_units=({"operation": "qfq_status", "trade_date": DAY},),
    )

    assert spec.component_missing_units[0]["component"] == "qfq"
    assert spec.units == ({"operation": "qfq_status", "trade_date": DAY},)


def test_quant_refresh_constructs_tushare_without_consulting_environment_factory(monkeypatch):
    code = _quant_codes(1)[0]
    spec = refresh.RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, DAY, (code,),
        operation_units=({"operation": "qfq_status", "trade_date": DAY},),
    )
    provider = object()

    class Active:
        connection = object()
        resources = ()

        @staticmethod
        def provider(value):
            return value

        @staticmethod
        def assert_alive():
            return None

    @contextmanager
    def scope(*_args, **_kwargs):
        yield Active()

    monkeypatch.setattr(refresh, "ingestion_scope", scope)
    monkeypatch.setattr(refresh, "_missing_units", lambda *_args, **_kwargs: {("qfq_status", "", DAY)})
    monkeypatch.setattr(refresh, "_providers_from_env",
                        lambda: pytest.fail("private quant resource must not use configured source"))
    monkeypatch.setenv("LIVEPROFIT_DATA_SOURCE", "akshare")
    monkeypatch.setattr("AI.dataflows.providers.cn.tushare.TushareProvider", lambda: provider)
    calls = []
    monkeypatch.setattr(refresh, "_collect_refresh_unlocked",
                        lambda conn, value, progress, source, fallback: calls.append(source) or {})

    refresh.collect_refresh(object(), spec)

    assert calls == [provider]


def test_technical_factor_fetch_falls_back_to_scoped_100_code_batches(monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)
    codes = _quant_codes(205)
    returned = []

    def load(day, ts_codes=None):
        if ts_codes is None:
            return pd.DataFrame({"ts_code": [codes[0]] * 6000})
        returned.append(tuple(ts_codes))
        return _factor_frame(ts_codes)

    provider = SimpleNamespace(get_full_market_technical_factor_df=load)
    frame = fetch_stock_technical_factor_frame(provider, DAY.replace("-", ""), codes)

    assert [len(batch) for batch in returned] == [100, 100, 5]
    assert set(frame["ts_code"]) == set(codes)


def test_technical_factor_fallback_rejects_code_outside_requested_batch(monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)
    codes = _quant_codes(1)
    calls = 0

    def load(day, ts_codes=None):
        nonlocal calls
        calls += 1
        if ts_codes is None:
            return pd.DataFrame({"ts_code": [codes[0]] * 6000})
        return _factor_frame(["999999.SZ"])

    with pytest.raises(StoreFetchError, match="UPSTREAM_CODE_MISMATCH"):
        fetch_stock_technical_factor_frame(
            SimpleNamespace(get_full_market_technical_factor_df=load),
            DAY.replace("-", ""), codes,
        )
    assert calls == 2


def test_technical_factor_fallback_rejects_still_truncated_batch(monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)
    codes = _quant_codes(1)
    calls = 0

    def load(day, ts_codes=None):
        nonlocal calls
        calls += 1
        if ts_codes is None:
            return pd.DataFrame({"ts_code": [codes[0]] * 6000})
        return pd.DataFrame({"ts_code": [codes[0]] * 6000})

    with pytest.raises(StoreFetchError, match="UPSTREAM_INCOMPLETE"):
        fetch_stock_technical_factor_frame(
            SimpleNamespace(get_full_market_technical_factor_df=load),
            DAY.replace("-", ""), codes,
        )
    assert calls == 2


def test_adj_factor_day_group_writes_when_its_independent_95_percent_gate_passes(monkeypatch):
    codes = _quant_codes()
    frame = pd.DataFrame([{
        "ts_code": code, "trade_date": DAY, "adj_factor": 1.0,
    } for code in codes[:-1]])
    writes = []
    monkeypatch.setattr(refresh, "_pull_market_frame", lambda *_args: frame)
    monkeypatch.setattr(refresh, "bulk_upsert_factor",
                        lambda _conn, values, **_kwargs: writes.append(values.copy()) or len(values))
    conn = MagicMock()
    spec = refresh.RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, DAY, tuple(codes),
        component_thresholds={"daily": 1.0, "qfq": 0.95,
                              "adj_factor": 0.95, "trade_status": 0.95},
        operation_units=({"operation": "adj_factor", "trade_date": DAY},),
    )

    refresh._collect_quant_adj_factor(conn, object(), spec, DAY)

    assert len(writes) == 1 and len(writes[0]) == 19
    conn.commit.assert_called_once()


def test_adj_factor_invalid_values_do_not_write(monkeypatch):
    codes = _quant_codes()
    frame = pd.DataFrame([{
        "ts_code": code, "trade_date": DAY, "adj_factor": 1.0,
    } for code in codes])
    frame.loc[0, "adj_factor"] = float("inf")
    monkeypatch.setattr(refresh, "_pull_market_frame", lambda *_args: frame)
    write = MagicMock()
    monkeypatch.setattr(refresh, "bulk_upsert_factor", write)
    conn = MagicMock()
    spec = refresh.RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, DAY, tuple(codes),
        component_thresholds={"daily": 1.0, "qfq": 0.95,
                              "adj_factor": 0.95, "trade_status": 0.95},
        operation_units=({"operation": "adj_factor", "trade_date": DAY},),
    )

    with pytest.raises(ValueError, match="ADJ_FACTOR_INVALID_VALUE"):
        refresh._collect_quant_adj_factor(conn, object(), spec, DAY)

    write.assert_not_called()
    conn.commit.assert_not_called()


def test_qfq_and_status_commit_together_after_valid_95_percent_coverage(monkeypatch):
    codes = _quant_codes()
    written = {}
    monkeypatch.setattr(stock_factors, "fetch_stock_technical_factor_frame",
                        lambda *_args: _factor_frame(codes, warmup={codes[-1]}))

    def write_factor(_conn, frame, **_kwargs):
        written["factor"] = frame.copy()
        return len(frame)

    def write_status(_conn, frame, **_kwargs):
        written["status"] = frame.copy()
        return len(frame)

    monkeypatch.setattr(stock_factors, "bulk_upsert_factor_daily", write_factor)
    monkeypatch.setattr(stock_factors, "bulk_upsert_trade_status", write_status)
    monkeypatch.setattr(stock_factors.state_dao, "record_success", MagicMock())
    conn = MagicMock()

    result = stock_factors._collect_stock_quant_day_unlocked(
        conn, SimpleNamespace(get_full_market_trade_status_df=lambda _: _status_frame(codes)),
        DAY.replace("-", ""), codes,
    )

    assert result["status"] == "SUCCESS"
    assert result["coverage"] == 0.95
    assert len(written["factor"]) == 19  # warmup row is not persisted as a valid factor
    assert len(written["status"]) == 20
    conn.commit.assert_called_once()


def test_qfq_and_status_coverage_failure_writes_neither_table(monkeypatch):
    codes = _quant_codes()
    monkeypatch.setattr(stock_factors, "fetch_stock_technical_factor_frame",
                        lambda *_args: _factor_frame(codes[:-2]))
    factor_write = MagicMock()
    status_write = MagicMock()
    monkeypatch.setattr(stock_factors, "bulk_upsert_factor_daily", factor_write)
    monkeypatch.setattr(stock_factors, "bulk_upsert_trade_status", status_write)
    conn = MagicMock()

    with pytest.raises(ValueError, match="QUANT_COVERAGE_INCOMPLETE"):
        stock_factors._collect_stock_quant_day_unlocked(
            conn, SimpleNamespace(get_full_market_trade_status_df=lambda _: _status_frame(codes)),
            DAY.replace("-", ""), codes,
        )

    factor_write.assert_not_called()
    status_write.assert_not_called()
    conn.commit.assert_not_called()


def test_qfq_status_database_error_rolls_back_both_writes_and_ingest_state(monkeypatch):
    codes = _quant_codes()
    monkeypatch.setattr(stock_factors, "fetch_stock_technical_factor_frame",
                        lambda *_args: _factor_frame(codes))
    factor_write = MagicMock(return_value=len(codes))
    status_write = MagicMock(side_effect=RuntimeError("synthetic status write failure"))
    state_write = MagicMock()
    monkeypatch.setattr(stock_factors, "bulk_upsert_factor_daily", factor_write)
    monkeypatch.setattr(stock_factors, "bulk_upsert_trade_status", status_write)
    monkeypatch.setattr(stock_factors.state_dao, "record_success", state_write)
    conn = MagicMock()

    with pytest.raises(RuntimeError, match="synthetic status write failure"):
        stock_factors._collect_stock_quant_day_unlocked(
            conn, SimpleNamespace(get_full_market_trade_status_df=lambda _: _status_frame(codes)),
            DAY.replace("-", ""), codes,
        )

    factor_write.assert_called_once()
    status_write.assert_called_once()
    state_write.assert_not_called()
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_invalid_trade_status_value_blocks_both_quant_writes(monkeypatch):
    codes = _quant_codes(1)
    status = _status_frame(codes)
    status["is_suspended"] = status["is_suspended"].astype(object)
    status.loc[0, "is_suspended"] = "false"
    monkeypatch.setattr(stock_factors, "fetch_stock_technical_factor_frame",
                        lambda *_args: _factor_frame(codes))
    factor_write = MagicMock()
    status_write = MagicMock()
    monkeypatch.setattr(stock_factors, "bulk_upsert_factor_daily", factor_write)
    monkeypatch.setattr(stock_factors, "bulk_upsert_trade_status", status_write)
    conn = MagicMock()

    with pytest.raises(ValueError, match="STATUS_INVALID_BOOLEAN"):
        stock_factors._collect_stock_quant_day_unlocked(
            conn, SimpleNamespace(get_full_market_trade_status_df=lambda _: status),
            DAY.replace("-", ""), codes,
        )

    factor_write.assert_not_called()
    status_write.assert_not_called()
    conn.commit.assert_not_called()


@pytest.mark.parametrize(("case", "error_code"), [
    ("qfq_wrong_day", "QFQ_DATE_MISMATCH"),
    ("qfq_missing_column", "QFQ_COLUMNS_MISSING"),
    ("qfq_duplicate", "QFQ_INVALID_KEYS"),
    ("qfq_infinity", "QFQ_NONFINITE_VALUE"),
    ("status_wrong_day", "STATUS_DATE_MISMATCH"),
    ("status_duplicate", "STATUS_INVALID_KEYS"),
    ("status_infinite_limit", "STATUS_INVALID_LIMIT"),
    ("status_inverted_limits", "STATUS_INVALID_LIMIT_ORDER"),
])
def test_invalid_quant_frames_fail_before_either_table_is_written(monkeypatch, case, error_code):
    codes = _quant_codes(1)
    factor, status = _factor_frame(codes), _status_frame(codes)
    if case == "qfq_wrong_day":
        factor["trade_date"] = "2026-09-22"
    elif case == "qfq_missing_column":
        factor = factor.drop(columns=REQUIRED_QFQ[0])
    elif case == "qfq_duplicate":
        factor = pd.concat([factor, factor], ignore_index=True)
    elif case == "qfq_infinity":
        factor.loc[0, REQUIRED_QFQ[0]] = float("inf")
    elif case == "status_wrong_day":
        status["trade_date"] = "2026-09-22"
    elif case == "status_duplicate":
        status = pd.concat([status, status], ignore_index=True)
    elif case == "status_infinite_limit":
        status.loc[0, "up_limit"] = float("inf")
    elif case == "status_inverted_limits":
        status.loc[0, "up_limit"] = 8.0
        status.loc[0, "down_limit"] = 9.0
    monkeypatch.setattr(stock_factors, "fetch_stock_technical_factor_frame",
                        lambda *_args: factor)
    factor_write, status_write = MagicMock(), MagicMock()
    monkeypatch.setattr(stock_factors, "bulk_upsert_factor_daily", factor_write)
    monkeypatch.setattr(stock_factors, "bulk_upsert_trade_status", status_write)
    conn = MagicMock()

    with pytest.raises(ValueError, match=error_code):
        stock_factors._collect_stock_quant_day_unlocked(
            conn, SimpleNamespace(get_full_market_trade_status_df=lambda _: status),
            DAY.replace("-", ""), codes,
        )

    factor_write.assert_not_called()
    status_write.assert_not_called()
    conn.commit.assert_not_called()

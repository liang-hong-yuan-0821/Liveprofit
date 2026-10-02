"""量化股票 qfq 因子与交易状态的单日采集。"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from pandas.api.types import is_bool

from AI.dataflows.providers.base_provider import raise_if_network_access_denied
from db.instrument.dao import ingest_state as state_dao
from db.instrument.dao.factor_daily import bulk_upsert_factor_daily
from db.instrument.dao.trade_status import (
    bulk_upsert_trade_status,
    remove_ambiguous_tushare_status,
    upsert_observed_trade_status,
)
from db.instrument.ingest.frames import fetch_stock_technical_factor_frame
from db.instrument.ingest.guard import FATAL_INGEST_ERRORS, locked_ingestion

RESOURCE_STOCK_FACTORS = "stock_factor_qfq"
RESOURCE_TRADE_STATUS = "stock_trade_status"
SOURCE_TUSHARE = "tushare"
MIN_GLOBAL_COVERAGE = 0.95
REQUIRED_QFQ = [
    "ma_qfq_5", "ma_qfq_20", "ma_qfq_60", "boll_mid_qfq", "boll_upper_qfq",
    "boll_lower_qfq", "macd_dif_qfq", "macd_dea_qfq", "macd_qfq", "rsi_qfq_6",
    "atr_qfq",
]


def _codes_hash(codes: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(codes)).encode("utf-8")).hexdigest()


def collect_stock_quant_day(conn, provider, trade_date: str, active_codes: list[str]) -> dict:
    """采集并原子写入单日 qfq 因子与交易状态；失败不覆盖旧成功水位。"""
    expected = set(active_codes)
    factor = fetch_stock_technical_factor_frame(provider, trade_date, active_codes)
    status = provider.get_full_market_trade_status_df(trade_date)
    raise_if_network_access_denied(provider)
    if status is None or status.empty or len(status) >= 6000:
        raise ValueError("UPSTREAM_NOT_READY")
    ambiguous_codes = set(status.attrs.get("ambiguous_codes", ())) & set(active_codes)
    factor = factor.copy()
    status = status.copy()
    required_factor = {"ts_code", "trade_date", *REQUIRED_QFQ}
    missing_columns = sorted(required_factor - set(factor.columns))
    if missing_columns:
        raise ValueError("QFQ_COLUMNS_MISSING: " + ",".join(missing_columns))
    required_status = {"ts_code", "trade_date", "is_suspended", "is_st", "market_board"}
    missing_columns = sorted(required_status - set(status.columns))
    if missing_columns:
        raise ValueError("STATUS_COLUMNS_MISSING: " + ",".join(missing_columns))

    for frame, label in ((factor, "QFQ"), (status, "STATUS")):
        days = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
        if days.isna().any() or not days.dt.date.eq(pd.Timestamp(trade_date).date()).all():
            raise ValueError(f"{label}_DATE_MISMATCH")
        if frame["ts_code"].isna().any() or frame.duplicated(["ts_code", "trade_date"]).any():
            raise ValueError(f"{label}_INVALID_KEYS")
        frame["trade_date"] = days.dt.strftime("%Y-%m-%d")

    factor = factor[factor["ts_code"].astype(str).isin(expected)].copy()
    status = status[status["ts_code"].astype(str).isin(expected)].copy()
    if factor.empty or status.empty:
        raise ValueError("UPSTREAM_NOT_READY")
    for column in REQUIRED_QFQ:
        raw = factor[column]
        factor[column] = pd.to_numeric(raw, errors="coerce")
        if (raw.notna() & factor[column].isna()).any():
            raise ValueError("QFQ_INVALID_VALUE")
        values = factor[column].dropna().to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("QFQ_NONFINITE_VALUE")
    eligible = factor.dropna(subset=REQUIRED_QFQ).copy()
    eligible_codes = set(eligible["ts_code"].astype(str))

    if not all(status[column].map(is_bool).all() for column in ("is_suspended", "is_st")):
        raise ValueError("STATUS_INVALID_BOOLEAN")
    boards = status["market_board"]
    if boards.isna().any() or boards.astype(str).str.strip().eq("").any():
        raise ValueError("STATUS_INVALID_MARKET_BOARD")
    for column in ("up_limit", "down_limit"):
        if column not in status.columns:
            status[column] = np.nan
        raw = status[column]
        status[column] = pd.to_numeric(raw, errors="coerce")
        if (raw.notna() & status[column].isna()).any():
            raise ValueError("STATUS_INVALID_LIMIT")
        values = status[column].dropna().to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("STATUS_INVALID_LIMIT")
        # stk_limit may report 0 for an unavailable limit (observed on BJ
        # stocks). Keep the limit unknown; zero is not an executable price.
        status.loc[status[column].eq(0), column] = np.nan
    both_limits = status["up_limit"].notna() & status["down_limit"].notna()
    if (status.loc[both_limits, "up_limit"] < status.loc[both_limits, "down_limit"]).any():
        raise ValueError("STATUS_INVALID_LIMIT_ORDER")
    status_codes = set(status["ts_code"].astype(str))
    coverage = len(eligible_codes) / len(expected) if expected else 1.0
    status_coverage = len(status_codes) / len(expected) if expected else 1.0
    # 单日因子源会合法缺少新股暖机值或极少数停牌/退市边界代码。全局门禁只要求
    # 数据帧未被截断（>=95%）；逐模板的实际字段/窗口完整性由执行期 loader
    # fail-closed 为 WARMUP_INCOMPLETE / INDICATOR_UNAVAILABLE。禁止因为几十只合法
    # 暖机股票而拒绝写入其余五千余只完整标的的数据。
    if coverage < MIN_GLOBAL_COVERAGE or status_coverage < MIN_GLOBAL_COVERAGE:
        raise ValueError("QUANT_COVERAGE_INCOMPLETE")
    eligible["updated_at"] = pd.Timestamp.now(tz="UTC")
    status["source"] = SOURCE_TUSHARE
    status["updated_at"] = pd.Timestamp.now(tz="UTC")
    try:
        n_factor = bulk_upsert_factor_daily(conn, eligible, update=True)
        n_status = bulk_upsert_trade_status(conn, status, update=True)
        remove_ambiguous_tushare_status(conn, trade_date, ambiguous_codes)
        summary = {
            "trade_date": trade_date, "active": len(expected), "factor_rows": n_factor,
            "status_rows": n_status, "coverage": coverage, "status_coverage": status_coverage,
        }
        state_dao.record_success(
            conn, RESOURCE_STOCK_FACTORS, SOURCE_TUSHARE,
            coverage=coverage, member_hash=_codes_hash(eligible_codes & expected), summary=summary,
        )
        if not ambiguous_codes:
            state_dao.record_success(
                conn, RESOURCE_TRADE_STATUS, SOURCE_TUSHARE,
                coverage=status_coverage, member_hash=_codes_hash(status_codes & expected), summary=summary,
            )
        conn.commit()
        return {"status": "PARTIAL" if ambiguous_codes else "SUCCESS",
                "ambiguous_codes": sorted(ambiguous_codes), **summary}
    except FATAL_INGEST_ERRORS:
        raise
    except Exception:
        conn.rollback()
        raise


def _collect_stock_status_day_unlocked(conn, provider, trade_date: str,
                                      active_codes: list[str] | None = None,
                                      st_observations=None, no_trade_observations=None) -> dict:
    """Independent trusted status observations, without qfq/ingest_state writes."""
    source_inputs = {}
    if st_observations is not None:
        source_inputs["st_observations"] = st_observations
    if no_trade_observations is not None:
        source_inputs["no_trade_observations"] = no_trade_observations
    status = provider.get_full_market_trade_status_df(trade_date, **source_inputs)
    raise_if_network_access_denied(provider)
    if status is None or status.empty or len(status) >= 6000:
        return {"status": "UNAVAILABLE", "rows": 0, "trade_date": trade_date}
    ambiguous_codes = set(status.attrs.get("ambiguous_codes", ()))
    st_source = status.attrs.get("st_source", "tushare")
    if st_source not in ("tushare", "baostock_kline"):
        return {"status": "UNAVAILABLE", "rows": 0, "code": "STATUS_ST_SOURCE_INVALID"}
    required = {"ts_code", "trade_date", "is_suspended", "is_st", "market_board"}
    if not required.issubset(status.columns):
        return {"status": "UNAVAILABLE", "rows": 0, "code": "STATUS_COLUMNS_MISSING"}
    days = pd.to_datetime(status["trade_date"].astype(str), errors="coerce")
    if days.isna().any() or not days.dt.date.eq(pd.Timestamp(trade_date).date()).all():
        return {"status": "UNAVAILABLE", "rows": 0, "code": "STATUS_DATE_MISMATCH"}
    if status.duplicated(["ts_code", "trade_date"]).any():
        return {"status": "UNAVAILABLE", "rows": 0, "code": "STATUS_DUPLICATE_KEYS"}
    status = status.copy()
    if active_codes is not None:
        status = status[status["ts_code"].isin(active_codes)].copy()
        ambiguous_codes &= set(active_codes)
    status["source"] = (SOURCE_TUSHARE if st_source == "tushare" and not status.attrs.get("uses_independent_trading", False)
                        else "tushare+baostock")
    status["updated_at"] = pd.Timestamp.now(tz="UTC")
    try:
        rows = upsert_observed_trade_status(conn, status)
        remove_ambiguous_tushare_status(conn, trade_date, ambiguous_codes)
        conn.commit()
        return {"status": "PARTIAL" if ambiguous_codes else ("SUCCESS" if rows else "UNAVAILABLE"),
                "rows": rows, "trade_date": trade_date,
                "source": status["source"].iloc[0] if rows else None,
                "ambiguous_codes": sorted(ambiguous_codes)}
    except FATAL_INGEST_ERRORS:
        raise
    except ValueError:
        conn.rollback()
        return {"status": "UNAVAILABLE", "rows": 0, "code": "STATUS_INVALID"}
    except Exception:
        conn.rollback()
        raise


collect_stock_status_day = locked_ingestion("CN_STOCK_DAILY")(_collect_stock_status_day_unlocked)


# Public entrypoints acquire once; nested collectors reuse the guarded connection.
_collect_stock_quant_day_unlocked = collect_stock_quant_day
collect_stock_quant_day = locked_ingestion("CN_STOCK_DAILY")(_collect_stock_quant_day_unlocked)

"""量化股票 qfq 因子与交易状态的单日采集。"""

from __future__ import annotations

import hashlib

import pandas as pd

from db.instrument.dao.factor_daily import bulk_upsert_factor_daily
from db.instrument.dao.trade_status import bulk_upsert_trade_status
from db.instrument.dao import ingest_state as state_dao

RESOURCE_STOCK_FACTORS = "stock_factor_qfq"
RESOURCE_TRADE_STATUS = "stock_trade_status"
SOURCE_TUSHARE = "tushare"
MIN_GLOBAL_COVERAGE = 0.95
REQUIRED_QFQ = [
    "ma_qfq_5", "ma_qfq_20", "ma_qfq_60", "boll_mid_qfq", "boll_upper_qfq",
    "boll_lower_qfq", "macd_dif_qfq", "macd_dea_qfq", "macd_qfq", "rsi_qfq_6",
]


def _codes_hash(codes: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(codes)).encode("utf-8")).hexdigest()


def collect_stock_quant_day(conn, provider, trade_date: str, active_codes: list[str]) -> dict:
    """采集并原子写入单日 qfq 因子与交易状态；失败不覆盖旧成功水位。"""
    factor = provider.get_full_market_technical_factor_df(trade_date)
    status = provider.get_full_market_trade_status_df(trade_date)
    if factor is None or status is None:
        return {"status": "UNAVAILABLE", "trade_date": trade_date}
    expected = set(active_codes)
    factor = factor.copy()
    status = status.copy()
    missing_columns = [c for c in REQUIRED_QFQ if c not in factor.columns]
    if missing_columns:
        return {"status": "FAILED", "code": "QFQ_COLUMNS_MISSING", "missing_columns": missing_columns}
    factor_codes = set(factor.get("ts_code", pd.Series(dtype=str)).astype(str))
    status_codes = set(status.get("ts_code", pd.Series(dtype=str)).astype(str))
    eligible = factor.dropna(subset=REQUIRED_QFQ)
    eligible_codes = set(eligible["ts_code"].astype(str))
    coverage = len(eligible_codes & expected) / len(expected) if expected else 1.0
    status_coverage = len(status_codes & expected) / len(expected) if expected else 1.0
    # 单日因子源会合法缺少新股暖机值或极少数停牌/退市边界代码。全局门禁只要求
    # 数据帧未被截断（>=95%）；逐模板的实际字段/窗口完整性由执行期 loader
    # fail-closed 为 WARMUP_INCOMPLETE / INDICATOR_UNAVAILABLE。禁止因为几十只合法
    # 暖机股票而拒绝写入其余五千余只完整标的的数据。
    if coverage < MIN_GLOBAL_COVERAGE or status_coverage < MIN_GLOBAL_COVERAGE:
        return {
            "status": "FAILED", "code": "QUANT_COVERAGE_INCOMPLETE",
            "coverage": coverage, "status_coverage": status_coverage,
            "missing_factor_codes": sorted(expected - factor_codes)[:100],
            "missing_status_codes": sorted(expected - status_codes)[:100],
        }
    factor["updated_at"] = pd.Timestamp.now(tz="UTC")
    status["source"] = SOURCE_TUSHARE
    status["updated_at"] = pd.Timestamp.now(tz="UTC")
    try:
        n_factor = bulk_upsert_factor_daily(conn, factor, update=True)
        n_status = bulk_upsert_trade_status(conn, status, update=True)
        summary = {
            "trade_date": trade_date, "active": len(expected), "factor_rows": n_factor,
            "status_rows": n_status, "coverage": coverage, "status_coverage": status_coverage,
        }
        state_dao.record_success(
            conn, RESOURCE_STOCK_FACTORS, SOURCE_TUSHARE,
            coverage=coverage, member_hash=_codes_hash(eligible_codes & expected), summary=summary,
        )
        state_dao.record_success(
            conn, RESOURCE_TRADE_STATUS, SOURCE_TUSHARE,
            coverage=status_coverage, member_hash=_codes_hash(status_codes & expected), summary=summary,
        )
        conn.commit()
        return {"status": "SUCCESS", **summary}
    except Exception:
        conn.rollback()
        raise

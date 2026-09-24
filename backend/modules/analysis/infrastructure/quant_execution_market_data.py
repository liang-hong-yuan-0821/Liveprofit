"""量化执行期行情读取（plan 4.3.1，行情不冻结、执行时实时读取）。

- AllMarketUniverseBuilder.list_active_cn_stocks(conn)：每次执行开始时通过
  db.instrument.dao.instrument 的共用查询，从 market.instrument 枚举活跃 CN 股票；
  不使用板块短名单、相对强度或成交额预过滤。量化补齐覆盖分母复用同一查询。
- MarketContextBatchLoader.load_batch(conn, ts_codes, effective_trade_date, lookback)：
  每批参数化 = ANY(:codes) 查询 instrument_daily / factor_daily，每票固定取 250 根 bars；
  少于 250 根该票记 DATA_UNAVAILABLE（执行时判定，不落快照）；因子行缺失记
  INDICATOR_UNAVAILABLE。连接绝不进入 State 或子进程。
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math

from AI.strategy_sandbox.protocol import build_context
from db.instrument.dao.instrument import list_active_cn_stocks

DEFAULT_LOOKBACK = 250


def _trade_date_key(value) -> str:
    """把数据库驱动可能返回的 date/datetime/字符串统一为 ISO 日期键。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _finite_number(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


class AllMarketUniverseBuilder:
    """执行期读模型：实时枚举活跃 CN 股票（不是 Graph 节点）。"""

    @staticmethod
    def list_active_cn_stocks(conn) -> list[str]:
        return list_active_cn_stocks(conn)


class MarketContextBatchLoader:
    """执行期批量行情读取：200 code 参数化批量，批完即弃。"""

    def __init__(self, *, lookback: int = DEFAULT_LOOKBACK) -> None:
        self._lookback = lookback

    def load_batch(
        self,
        conn,
        ts_codes: list[str],
        effective_trade_date: date,
        *,
        positions_by_code: dict[str, dict] | None = None,
        requested_trade_date: date | None = None,
    ) -> list[dict]:
        """返回每票一项：{ts_code, context|None, status}。

        status 取值 OK / DATA_UNAVAILABLE（少于 250 根）/ INDICATOR_UNAVAILABLE（因子无行）。
        """
        positions_by_code = positions_by_code or {}
        results: list[dict] = []
        if not ts_codes:
            return results
        upper = effective_trade_date.isoformat()

        daily_rows = conn.execute(
            "WITH ranked AS (SELECT ts_code, trade_date, open, high, low, close, vol, amount, "
            "row_number() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn "
            "FROM market.instrument_daily WHERE ts_code = ANY(%s) AND trade_date <= %s) "
            "SELECT ts_code, trade_date, open, high, low, close, vol, amount "
            "FROM ranked WHERE rn <= %s ORDER BY ts_code, trade_date DESC",
            (ts_codes, upper, self._lookback),
        ).fetchall()
        factor_rows = conn.execute(
            "WITH ranked AS (SELECT ts_code, trade_date, ma_qfq_5, ma_qfq_20, ma_qfq_60, "
            "boll_mid_qfq, boll_upper_qfq, boll_lower_qfq, macd_dif_qfq, macd_dea_qfq, "
            "macd_qfq, rsi_qfq_6, row_number() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn "
            "FROM market.factor_daily WHERE ts_code = ANY(%s) AND trade_date <= %s) "
            "SELECT ts_code, trade_date, ma_qfq_5, ma_qfq_20, ma_qfq_60, boll_mid_qfq, "
            "boll_upper_qfq, boll_lower_qfq, macd_dif_qfq, macd_dea_qfq, macd_qfq, rsi_qfq_6 "
            "FROM ranked WHERE rn <= %s ORDER BY ts_code, trade_date DESC",
            (ts_codes, upper, self._lookback),
        ).fetchall()
        adj_rows = conn.execute(
            "WITH ranked AS (SELECT ts_code, trade_date, adj_factor, "
            "row_number() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn "
            "FROM market.adj_factor WHERE ts_code = ANY(%s) AND trade_date <= %s) "
            "SELECT ts_code, trade_date, adj_factor FROM ranked WHERE rn <= %s "
            "ORDER BY ts_code, trade_date DESC",
            (ts_codes, upper, self._lookback),
        ).fetchall()
        status_rows = conn.execute(
            "SELECT ts_code, trade_date, is_suspended, is_st, up_limit, down_limit, market_board "
            "FROM market.trade_status_daily WHERE ts_code = ANY(%s) AND trade_date = %s",
            (ts_codes, upper),
        ).fetchall()

        daily_by_code: dict[str, list] = {}
        for r in daily_rows:
            daily_by_code.setdefault(r[0], []).append(r)
        factor_by_code: dict[str, list] = {}
        for r in factor_rows:
            factor_by_code.setdefault(r[0], []).append(r)
        adj_by_code: dict[str, dict[str, object]] = {}
        for r in adj_rows:
            adj_by_code.setdefault(r[0], {})[_trade_date_key(r[1])] = r[2]
        status_by_code = {r[0]: r for r in status_rows}

        for ts_code in ts_codes:
            daily = daily_by_code.get(ts_code, [])
            factors = factor_by_code.get(ts_code, [])
            if not daily:
                results.append({"ts_code": ts_code, "context": None, "status": "DATA_UNAVAILABLE"})
                continue
            latest_bar = _trade_date_key(daily[0][1])
            if latest_bar != upper:
                results.append({"ts_code": ts_code, "context": None, "status": "STALE_DATA"})
                continue
            if len(daily) < self._lookback:
                results.append({"ts_code": ts_code, "context": None, "status": "WARMUP_INCOMPLETE"})
                continue
            if not factors:
                results.append({"ts_code": ts_code, "context": None, "status": "INDICATOR_UNAVAILABLE"})
                continue
            # 取最近 lookback 根（降序列表前 N 根）并反转为交易日升序
            bars = list(reversed(daily[: self._lookback]))
            # 因子严格按 (ts_code, trade_date) 对齐。数据库驱动可能分别返回
            # date 与字符串，因此两侧先归一为 ISO 日期键；禁止按行号或整条 bar 查询。
            factor_map = {_trade_date_key(r[1]): tuple(r[2:12]) for r in factors}
            dates = [_trade_date_key(r[1]) for r in bars]
            aligned = [factor_map.get(d, (None,) * 10) for d in dates]
            # 最新交易日必须存在同日因子行；具体哪些列必须非空由模板的
            # required_fields/实际下标门禁决定，不能用十项全非空误伤只读 MA5 的模板。
            if not any(_finite_number(v) for v in aligned[-1]):
                results.append({"ts_code": ts_code, "context": None, "status": "INDICATOR_UNAVAILABLE"})
                continue
            adj_map = adj_by_code.get(ts_code, {})
            base_adj = adj_map.get(upper)
            if not _finite_number(base_adj) or float(base_adj) <= 0:
                results.append({"ts_code": ts_code, "context": None, "status": "ADJ_FACTOR_UNAVAILABLE"})
                continue
            if any(not _finite_number(adj_map.get(d)) or float(adj_map[d]) <= 0 for d in dates):
                results.append({"ts_code": ts_code, "context": None, "status": "ADJ_FACTOR_UNAVAILABLE"})
                continue
            status_row = status_by_code.get(ts_code)
            if status_row is None:
                results.append({"ts_code": ts_code, "context": None, "status": "TRADE_STATUS_UNAVAILABLE"})
                continue
            qfq = []
            for row, d in zip(bars, dates):
                ratio = float(adj_map[d]) / float(base_adj)
                qfq.append(tuple(float(row[i]) * ratio if row[i] is not None else None for i in range(2, 6)))
            position = positions_by_code.get(ts_code)
            if position is None:
                position = {"shares": 0, "average_cost": None, "market_value": 0}
            else:
                # 快照持仓键为 quantity/average_cost；统一为 shares/average_cost
                qty = position.get("shares", position.get("quantity", 0))
                avg = position.get("average_cost")
                market_value = round(float(qty) * float(bars[-1][5]), 4)
                position = {
                    "shares": float(qty),
                    "average_cost": float(avg) if avg is not None else None,
                    "market_value": market_value,
                }
            context = build_context(
                symbol=ts_code,
                effective_trade_date=upper,
                requested_trade_date=(requested_trade_date or effective_trade_date).isoformat(),
                market_as_of_trade_date=upper,
                latest_bar_trade_date=latest_bar,
                factor_trade_date=dates[-1],
                adj_factor_version=upper,
                ohlcv={
                    "trade_date": dates,
                    "open": [r[0] for r in qfq],
                    "high": [r[1] for r in qfq],
                    "low": [r[2] for r in qfq],
                    "close": [r[3] for r in qfq],
                    "volume": [float(r[6]) if r[6] is not None else None for r in bars],
                    "amount": [float(r[7]) if r[7] is not None else None for r in bars],
                },
                indicators={
                    "ma_qfq_5": [v[0] for v in aligned],
                    "ma_qfq_20": [v[1] for v in aligned],
                    "ma_qfq_60": [v[2] for v in aligned],
                    "boll_mid_qfq": [v[3] for v in aligned],
                    "boll_upper_qfq": [v[4] for v in aligned],
                    "boll_lower_qfq": [v[5] for v in aligned],
                    "macd_dif_qfq": [v[6] for v in aligned],
                    "macd_dea_qfq": [v[7] for v in aligned],
                    "macd_qfq": [v[8] for v in aligned],
                    "rsi_qfq_6": [v[9] for v in aligned],
                    # 旧已发布策略只读兼容；值仍来自 qfq，不再混用 bfq。
                    "ma_bfq_5": [v[0] for v in aligned],
                    "ma_bfq_20": [v[1] for v in aligned],
                    "rsi_bfq_6": [v[9] for v in aligned],
                },
                position=position,
                bars_count=self._lookback,
                price_basis="qfq",
                data_hash=hashlib.sha256(json.dumps(
                    {"dates": dates, "qfq": qfq, "indicators": aligned, "adj": str(base_adj)},
                    ensure_ascii=False, separators=(",", ":"), default=str,
                ).encode("utf-8")).hexdigest(),
            )
            results.append({
                "ts_code": ts_code,
                "context": context,
                "status": "OK",
                "execution_market": {
                    "trade_date": upper,
                    "raw_close": float(bars[-1][5]),
                    "qfq_close": float(qfq[-1][3]),
                    "raw_amount": float(bars[-1][7]) if bars[-1][7] is not None else None,
                    "adj_factor": float(base_adj),
                    "adj_factor_version": upper,
                    "is_suspended": bool(status_row[2]),
                    "is_st": bool(status_row[3]),
                    "up_limit": float(status_row[4]) if status_row[4] is not None else None,
                    "down_limit": float(status_row[5]) if status_row[5] is not None else None,
                    "market_board": status_row[6] if len(status_row) > 6 else None,
                },
            })
        return results

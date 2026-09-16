"""量化执行期行情读取（plan 4.3.1，行情不冻结、执行时实时读取）。

- AllMarketUniverseBuilder.list_active_cn_stocks(conn)：每次执行开始时以单次流式只读查询
  从 market.instrument 枚举活跃 CN 股票（instrument_type='stock' AND list_status='L'
  AND ts_code ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'），按 ts_code 升序；不使用板块短名单、
  相对强度或成交额预过滤。
- MarketContextBatchLoader.load_batch(conn, ts_codes, effective_trade_date, lookback)：
  每批参数化 = ANY(:codes) 查询 instrument_daily / factor_daily，每票固定取 250 根 bars；
  少于 250 根该票记 DATA_UNAVAILABLE（执行时判定，不落快照）；因子行缺失记
  INDICATOR_UNAVAILABLE。连接绝不进入 State 或子进程。
"""

from __future__ import annotations

from datetime import date

from AI.strategy_sandbox.protocol import build_context

DEFAULT_LOOKBACK = 250

CN_TS_FILTER = r"^[0-9]{6}\.(SH|SZ|BJ)$"
ACTIVE_STOCK_FILTER = f"instrument_type = 'stock' AND list_status = 'L' AND ts_code ~ '{CN_TS_FILTER}'"


class AllMarketUniverseBuilder:
    """执行期读模型：实时枚举活跃 CN 股票（不是 Graph 节点）。"""

    @staticmethod
    def list_active_cn_stocks(conn) -> list[str]:
        rows = conn.execute(
            f"SELECT ts_code FROM market.instrument WHERE {ACTIVE_STOCK_FILTER} ORDER BY ts_code"
        ).fetchall()
        return [r[0] for r in rows]


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
            "SELECT ts_code, trade_date, open, high, low, close, vol, amount "
            "FROM market.instrument_daily "
            "WHERE ts_code = ANY(%s) AND trade_date <= %s "
            "ORDER BY ts_code, trade_date DESC",
            (ts_codes, upper),
        ).fetchall()
        factor_rows = conn.execute(
            "SELECT ts_code, trade_date, ma_bfq_5, ma_bfq_20, rsi_bfq_6 "
            "FROM market.factor_daily "
            "WHERE ts_code = ANY(%s) AND trade_date <= %s "
            "ORDER BY ts_code, trade_date DESC",
            (ts_codes, upper),
        ).fetchall()

        daily_by_code: dict[str, list] = {}
        for r in daily_rows:
            daily_by_code.setdefault(r[0], []).append(r)
        factor_by_code: dict[str, list] = {}
        for r in factor_rows:
            factor_by_code.setdefault(r[0], []).append(r)

        for ts_code in ts_codes:
            daily = daily_by_code.get(ts_code, [])
            factors = factor_by_code.get(ts_code, [])
            if len(daily) < self._lookback:
                results.append({"ts_code": ts_code, "context": None, "status": "DATA_UNAVAILABLE"})
                continue
            if not factors:
                results.append({"ts_code": ts_code, "context": None, "status": "INDICATOR_UNAVAILABLE"})
                continue
            # 取最近 lookback 根（降序列表前 N 根）并反转为交易日升序
            bars = list(reversed(daily[: self._lookback]))
            # 因子按日期对齐（不足的日期填 null，按行 null 对齐，绝不前填）
            factor_map = {r[1]: (r[2], r[3], r[4]) for r in factors}
            dates = [str(r[1]) for r in bars]
            ma5 = [factor_map.get(d, (None, None, None))[0] for d in bars]
            ma20 = [factor_map.get(d, (None, None, None))[1] for d in bars]
            rsi6 = [factor_map.get(d, (None, None, None))[2] for d in bars]
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
                ohlcv={
                    "trade_date": dates,
                    "open": [float(r[2]) if r[2] is not None else None for r in bars],
                    "high": [float(r[3]) if r[3] is not None else None for r in bars],
                    "low": [float(r[4]) if r[4] is not None else None for r in bars],
                    "close": [float(r[5]) if r[5] is not None else None for r in bars],
                    "volume": [float(r[6]) if r[6] is not None else None for r in bars],
                    "amount": [float(r[7]) if r[7] is not None else None for r in bars],
                },
                indicators={"ma_bfq_5": ma5, "ma_bfq_20": ma20, "rsi_bfq_6": rsi6},
                position=position,
                bars_count=self._lookback,
            )
            results.append({"ts_code": ts_code, "context": context, "status": "OK"})
        return results

"""MarketDataService：Bars 查询与热点现场计算（证券市场数据库统一方案 3.3.1）。

- 市场数据读路径唯一连接源 = market_conn（db.instrument.get_connection 工厂）；
  SQLAlchemy 侧无残留读模型（旧 ORM/仓库已随方案 3.6.1 删除）
- freshness_status 与 market_session_status 正交：休市不是错误。
- 热度现场计算（决策 13）：读 market.sector_daily 按 heat_v1 公式现算，
  不落快照表、不进 Redis；公式与 provider 直连版互指同步（polish a）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backend.modules.market_data.application.errors import (
    IntervalNotSupportedError,
    MarketAssetNotFoundError,
    RangeTooLargeError,
)
from backend.shared.clock import Clock, SystemClock

# 技术指标不自算（技术指标数据源切换方案 §3.3）：指标值取自 idx_factor_pro 入库数据
# （上游用区间前历史计算，因子行自带全历史窗口），无需补窗口。
MA_PERIODS = (5, 10, 20, 60)
BOLL_PERIOD = 20
BOLL_K = 2.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# 热度计算口径（heat_v1，与 AI/dataflows/providers/cn/tushare.py 的
# _fetch_concept_heat 同公式——两处口径注释互指，变更须同步，polish a）
HEAT_ALGORITHM_VERSION = "heat_v1"
HEAT_PRICE_WEIGHT = 0.6
HEAT_VOL_WEIGHT = 0.4
HEAT_WINDOW_DAYS = 10        # 近 10 日区间（查询窗口近 20 交易日取 tail(11)）
HEAT_QUERY_DAYS = 20

# 概念树成员截断上限（板块概念Treemap方案 3.2：热度榜偏向成分多的大板块——
# dc 实测 216 板块 >100 成分、top 30 合计 29,062 成分，全量返回 2–3MB 且前端
# 2.7 万节点不可渲染；每概念按 |pct_chg| 降序截断，member_total 标全量数）
MEMBERS_PER_CONCEPT = 100


@dataclass(frozen=True)
class AssetDTO:
    """Bars 响应资产信息（裁剪定稿：market/symbol/name 三字段，R1 minor 2）。"""

    market: str
    symbol: str
    name: str


@dataclass(frozen=True)
class BarsDTO:
    asset: AssetDTO
    interval: str
    from_date: date
    to_date: date
    bars: list[dict]
    indicators: dict | None  # MA/BOLL/MACD 并列数组（idx_factor_pro 入库数据），与 bars 等长按 index 对齐；bars 空时 None
    source: str | None
    as_of: date | None
    source_updated_at: object
    freshness_status: str  # FRESH/STALE/UNAVAILABLE
    market_session_status: str  # OPEN/CLOSED
    market_closed_reason: str | None


class MarketDataService:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        calendar=None,
        market_conn=None,
    ) -> None:
        """market_conn = db.instrument.get_connection 工厂（contextmanager）。

        uow 参数已删除（决策 13）：热点快照表整链删除后 SQLAlchemy 侧无读模型，
        market_conn 成为市场数据读路径唯一连接源。
        """
        self._clock = clock or SystemClock()
        self._calendar = calendar
        self._market_conn = market_conn

    # ---- K 线 ----

    def get_bars(
        self, *, market: str, symbol: str, interval: str, from_date: date, to_date: date
    ) -> BarsDTO:
        # from>to 校验保留（K 线方案把原"超 365 天"用例替换为 from>to——
        # 365 上限已由该方案移除，本方案保留 from>to 拒绝语义）
        if from_date > to_date:
            raise RangeTooLargeError("查询范围无效：开始日期晚于结束日期")
        if interval != "1d":
            raise IntervalNotSupportedError(f"该资产不支持周期：{interval}")

        # market 形参保留仅契约兼容（回显），不参与查询条件——instrument 无 market 列
        # （决策 9②）；资产存在性 = instrument 查询（instrument_type ∈ index/stock——
        # 板块概念Treemap方案 3.3 放宽个股，fund 仍 404）
        from db.instrument.dao.instrument import get_instrument
        with self._market_conn() as conn:
            inst = get_instrument(conn, symbol)
            bars_full = None
            latest = None
            source_updated = None
            if inst is not None and inst["instrument_type"] in ("index", "stock"):
                from db.instrument.dao import instrument_daily
                df = instrument_daily.query_range(
                    conn, symbol, from_date.isoformat(), to_date.isoformat())
                bars_full = df
                latest_row = conn.execute(
                    "SELECT max(trade_date), max(updated_at) "
                    "FROM market.instrument_daily WHERE ts_code = %s",
                    (symbol,),
                ).fetchone()
                latest = latest_row[0] if latest_row else None
                source_updated = latest_row[1] if latest_row else None

        if inst is None:
            raise MarketAssetNotFoundError(f"资产不存在：{market} {symbol}")
        # 非 index/stock 类型（如基金代码误传入）同样 404 语义
        if inst["instrument_type"] not in ("index", "stock"):
            raise MarketAssetNotFoundError(f"资产不存在：{market} {symbol}")

        freshness = "UNAVAILABLE"
        if latest is not None:
            last_trading = self._last_trading_day()
            freshness = "FRESH" if last_trading is not None and latest >= last_trading else "STALE"

        session_status, closed_reason = self._session_status()

        bar_dicts = []
        if bars_full is not None and not bars_full.empty:
            bar_dicts = [
                {
                    "timestamp": row["trade_date"],
                    "open": float(row["open"]) if pd.notna(row["open"]) else None,
                    "high": float(row["high"]) if pd.notna(row["high"]) else None,
                    "low": float(row["low"]) if pd.notna(row["low"]) else None,
                    "close": float(row["close"]) if pd.notna(row["close"]) else None,
                    "volume": float(row["vol"]) if pd.notna(row["vol"]) else None,
                }
                for _, row in bars_full.iterrows()
            ]
        indicators = None
        if bar_dicts:
            with self._market_conn() as conn:
                from db.instrument.dao.factor_daily import query_range as factors_range
                fdf = factors_range(conn, symbol, from_date.isoformat(), to_date.isoformat())
            # 仅个股路径短路（板块概念Treemap方案 3.3）：个股因子采集为后续阶段，
            # 因子表无行 → indicators=None 纯 K 线；指数路径保持全 null 数组降级
            # 语义（契约测试 test_market_data.py:77-81 冻结断言，不得全局改 None）
            if not (inst["instrument_type"] == "stock" and fdf.empty):
                factor_map = {r["trade_date"]: r for _, r in fdf.iterrows()} if not fdf.empty else {}

                def _factor_col(name: str) -> list[float | None]:
                    # 判值不判行：行存在但列为 NULL 与行缺失同为 None，不抛异常
                    values: list[float | None] = []
                    for b in bar_dicts:
                        row = factor_map.get(b["timestamp"])
                        value = row[name] if row is not None else None
                        values.append(float(value) if value is not None else None)
                    return values

                indicators = {
                    "ma": [
                        {"period": p, "values": _factor_col(f"ma_bfq_{p}")}
                        for p in MA_PERIODS
                    ],
                    "boll": {
                        "period": BOLL_PERIOD,
                        "k": BOLL_K,
                        "mid": _factor_col("boll_mid_bfq"),
                        "upper": _factor_col("boll_upper_bfq"),
                        "lower": _factor_col("boll_lower_bfq"),
                    },
                    "macd": {
                        "fast": MACD_FAST,
                        "slow": MACD_SLOW,
                        "signal": MACD_SIGNAL,
                        "dif": _factor_col("macd_dif_bfq"),
                        "dea": _factor_col("macd_dea_bfq"),
                        "hist": _factor_col("macd_bfq"),
                    },
                }
        return BarsDTO(
            asset=AssetDTO(market=market, symbol=symbol, name=inst["name"]),
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            bars=bar_dicts,
            indicators=indicators,
            source=inst["data_source"],
            as_of=latest,
            source_updated_at=source_updated,  # 最近一次入库时间（datetime）
            freshness_status=freshness,
            market_session_status=session_status,
            market_closed_reason=closed_reason,
        )

    def get_sector_bars(
        self, *, market: str, source: str, sector_code: str,
        from_date: date, to_date: date,
    ) -> BarsDTO:
        """概念 K 线（板块概念Treemap方案 3.3）：读 market.sector_daily，indicators 恒 None。

        - from>to → RangeTooLargeError（同 get_bars）；板块不存在 → 404
        - OHLC 任一 NaN 整行不产出 bar（BarDTO OHLC 为必填 float，None 化会使
          响应 500；close 列 NOT NULL 已由 DAO 保障，本防御只拦脏行）
        """
        if from_date > to_date:
            raise RangeTooLargeError("查询范围无效：开始日期晚于结束日期")
        with self._market_conn() as conn:
            sector = conn.execute(
                "SELECT name FROM market.sector WHERE source = %s AND sector_code = %s",
                (source, sector_code),
            ).fetchone()
            bars_full = None
            latest = None
            source_updated = None
            if sector is not None:
                from db.instrument.dao.sector_daily import query_bars
                bars_full = query_bars(conn, source, sector_code,
                                       from_date.isoformat(), to_date.isoformat())
                latest_row = conn.execute(
                    "SELECT max(trade_date), max(updated_at) "
                    "FROM market.sector_daily WHERE source = %s AND sector_code = %s",
                    (source, sector_code),
                ).fetchone()
                latest = latest_row[0] if latest_row else None
                source_updated = latest_row[1] if latest_row else None

        if sector is None:
            raise MarketAssetNotFoundError(f"资产不存在：{market} {sector_code}")

        freshness = "UNAVAILABLE"
        if latest is not None:
            last_trading = self._last_trading_day()
            freshness = "FRESH" if last_trading is not None and latest >= last_trading else "STALE"

        session_status, closed_reason = self._session_status()

        bar_dicts = []
        if bars_full is not None and not bars_full.empty:
            for _, row in bars_full.iterrows():
                ohlc = (row["open"], row["high"], row["low"], row["close"])
                if any(pd.isna(v) for v in ohlc):
                    continue  # OHLC 任一 NaN 整行不产出（见 docstring）
                bar_dicts.append({
                    "timestamp": row["trade_date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["vol"]) if pd.notna(row["vol"]) else None,
                })
        return BarsDTO(
            asset=AssetDTO(market=market, symbol=sector_code, name=sector[0]),
            interval="1d",
            from_date=from_date,
            to_date=to_date,
            bars=bar_dicts,
            indicators=None,  # 板块指数无因子表数据（技术指标不自算）
            source=source,
            as_of=latest,
            source_updated_at=source_updated,
            freshness_status=freshness,
            market_session_status=session_status,
            market_closed_reason=closed_reason,
        )

    def _last_trading_day(self) -> date | None:
        if self._calendar is None:
            return None
        return self._calendar.last_trading_day(self._clock.now().date())

    def _session_status(self) -> tuple[str, str | None]:
        """开闭市与新鲜度正交：CLOSED 不是错误。无日历注入时默认 CLOSED（保守）。"""
        if self._calendar is None:
            return "CLOSED", "开闭市判定待交易日历接入"
        today = self._clock.now().date()
        if self._calendar.is_trading_day(today):
            return "OPEN", None
        return "CLOSED", "休市/已收盘"

    # ---- 热点现场计算（决策 13） ----

    def get_hot_concepts(
        self, *, market: str, as_of: date | None, limit: int
    ) -> tuple[date | None, list[dict], str, str, object]:
        """读 market.sector_daily 现场计算热度（heat_v1：pct×0.6 + vol_change×0.4）。

        - market≠CN 返回空态（sector_daily 仅 CN 板块数据，503 语义删除）
        - 无数据为正常业务态（NO_HOT_CONCEPTS，200 空 items）
        - as_of 省略时取 sector_daily（source='dc'）最新 trade_date——数据到哪算到哪
        - 窗口不足降级三段口径（M3 定稿，与 provider len(df) 分支逐段对齐）：
          ① ≤1 行：热度 = 最新行 pct_chg × 0.6；② 2 ≤ 行 ≤ 10：按可得行算 pct、
          vol_change=0；③ >10 行：完整公式
        """
        if market != "CN":
            return None, [], "NO_HOT_CONCEPTS", "STALE", None
        with self._market_conn() as conn:
            if as_of is None:
                row = conn.execute(
                    "SELECT max(trade_date) FROM market.sector_daily "
                    "WHERE source = 'dc'"
                ).fetchone()
                as_of = row[0] if row and row[0] is not None else None
            if as_of is None:
                return None, [], "NO_HOT_CONCEPTS", "STALE", None

            from db.instrument.dao.sector_daily import query_by_window
            start_row = conn.execute(
                "SELECT DISTINCT trade_date FROM market.sector_daily "
                "WHERE source = 'dc' AND trade_date <= %s "
                "ORDER BY trade_date DESC LIMIT %s",
                (as_of, HEAT_QUERY_DAYS),
            ).fetchall()
            if not start_row:
                return as_of, [], "NO_HOT_CONCEPTS", self._freshness(as_of), None
            window_start = start_row[-1][0]

            df = query_by_window(conn, "dc", window_start.isoformat(),
                                 as_of.isoformat())
            if df.empty:
                return as_of, [], "NO_HOT_CONCEPTS", self._freshness(as_of), None
            names = {r[0]: r[1] for r in conn.execute(
                "SELECT sector_code, name FROM market.sector "
                "WHERE source = 'dc'").fetchall()}

        # 逐板块计算（三段降级口径）
        top = self._compute_sector_heat(df)[:limit]

        items = []
        max_updated = None
        for rank, (code, _score, period_return, g) in enumerate(top, 1):
            # M5：daily_changes 只含有值条目（pct_chg NULL 的日跳过——
            # 契约 change_pct 非 Optional，NULL 会使整个响应 500）
            daily_changes = [
                {"date": str(r["trade_date"]),
                 "change_pct": float(r["pct_chg"])}
                for _, r in g.tail(HEAT_WINDOW_DAYS).iterrows()
                if pd.notna(r["pct_chg"])
            ]
            last_updated = g["updated_at"].dropna().iloc[-1] \
                if pd.notna(g["updated_at"]).any() else None
            if last_updated is not None and (max_updated is None or last_updated > max_updated):
                max_updated = last_updated
            items.append({
                "sector_code": code,
                "sector_name": names.get(code),
                "rank": rank,
                "hotness_reason": None,  # LLM 理由生成未实现，契约字段保留恒 NULL
                "period_return": period_return,
                "daily_changes": daily_changes,
                "updated_at": last_updated,  # 该板块行 updated_at（m5 定稿）
                "bars": [],  # 首版无板块 K 线读模型：卡片显示不可用状态（契约允许）
            })
        return as_of, items, "OK", self._freshness(as_of), max_updated

    def _compute_sector_heat(self, df) -> list[tuple]:
        """逐板块三段降级打分（heat_v1，get_hot_concepts 与 get_concept_tree 共用）。

        返回 [(code, score, period_return, g)] 已按 score 降序。三段口径与
        provider _fetch_concept_heat 的 len(df) 分支逐段对齐（统一方案 M3 定稿）。
        """
        scored = []
        for code, g in df.groupby("sector_code"):
            g = g.sort_values("trade_date").tail(HEAT_WINDOW_DAYS + 1)
            if len(g) <= 1:
                pct_chg = g["pct_chg"].iloc[-1]
                pct = float(pct_chg) if pd.notna(pct_chg) else 0.0
                score = pct * HEAT_PRICE_WEIGHT
                period_return = None
            else:
                closes = g["close"].astype(float).dropna()
                first_close = float(closes.iloc[0]) if not closes.empty else 0.0
                last_close = float(closes.iloc[-1]) if not closes.empty else 0.0
                pct = ((last_close - first_close) / first_close * 100
                       if first_close > 0 else 0.0)
                vols = g["vol"].astype(float)
                vol_change = 0.0
                if len(vols) > HEAT_WINDOW_DAYS:
                    recent_vol = float(vols.iloc[-HEAT_WINDOW_DAYS:].mean())
                    older_vol = float(vols.iloc[:-HEAT_WINDOW_DAYS].mean())
                    if older_vol > 0:
                        vol_change = (recent_vol - older_vol) / older_vol * 100
                score = pct * HEAT_PRICE_WEIGHT + vol_change * HEAT_VOL_WEIGHT
                period_return = pct
            scored.append((code, score, period_return, g))
        scored.sort(key=lambda x: -x[1])
        return scored

    def get_concept_tree(
        self, *, market: str, as_of: date | None, limit: int
    ) -> tuple[date | None, list[dict], str, str, object]:
        """概念树（板块概念Treemap方案 3.2）：热度 top N + as_of 当日涨跌幅 + 成分股。

        - market≠CN / 无数据 / 窗口不足：与 get_hot_concepts 同语义（NO_HOT_CONCEPTS）
        - heat_score 为 tree 契约新增字段（旧 hot 契约不加列）；pct_chg 显式按
          as_of 过滤取值（板块当日无行情行 → None，不得静默取更早日期）
        - members = dc 成分（LEFT JOIN instrument 取名称、缺失以 ts_code 兜底；
          LEFT JOIN instrument_daily 取当日横截面 pct_chg，停牌/无行 → None），
          按 |pct_chg| 降序截断 MEMBERS_PER_CONCEPT，member_total 标全量数
        """
        if market != "CN":
            return None, [], "NO_HOT_CONCEPTS", "STALE", None
        with self._market_conn() as conn:
            if as_of is None:
                row = conn.execute(
                    "SELECT max(trade_date) FROM market.sector_daily "
                    "WHERE source = 'dc'"
                ).fetchone()
                as_of = row[0] if row and row[0] is not None else None
            if as_of is None:
                return None, [], "NO_HOT_CONCEPTS", "STALE", None

            from db.instrument.dao.sector_daily import query_by_window
            start_row = conn.execute(
                "SELECT DISTINCT trade_date FROM market.sector_daily "
                "WHERE source = 'dc' AND trade_date <= %s "
                "ORDER BY trade_date DESC LIMIT %s",
                (as_of, HEAT_QUERY_DAYS),
            ).fetchall()
            if not start_row:
                return as_of, [], "NO_HOT_CONCEPTS", self._freshness(as_of), None
            window_start = start_row[-1][0]

            df = query_by_window(conn, "dc", window_start.isoformat(),
                                 as_of.isoformat())
            if df.empty:
                return as_of, [], "NO_HOT_CONCEPTS", self._freshness(as_of), None
            names = {r[0]: r[1] for r in conn.execute(
                "SELECT sector_code, name FROM market.sector "
                "WHERE source = 'dc'").fetchall()}

            top = self._compute_sector_heat(df)[:limit]
            top_codes = [code for code, _, _, _ in top]
            # 截断在 SQL 侧完成（ROW_NUMBER 窗口 + 外层 rn 过滤）——热度榜偏向成分多的
            # 大板块（dc 实测 top 30 合计 29,062 成分），Python 侧截断会把全部成分
            # （~2.9 万行）拉出库再排序，SQL 截断后出库量 ≤ limit×100 ≈ 3,000 行；
            # member_total 用 COUNT(*) OVER 同查询产出（截断不影响全量计数）
            member_rows = conn.execute(
                "SELECT sector_code, ts_code, name, pct_chg, member_total FROM ("
                "  SELECT m.sector_code, m.ts_code, "
                "         COALESCE(i.name, m.ts_code) AS name, d.pct_chg, "
                "         COUNT(*) OVER (PARTITION BY m.sector_code) AS member_total, "
                "         ROW_NUMBER() OVER (PARTITION BY m.sector_code "
                "           ORDER BY abs(d.pct_chg) DESC NULLS LAST, m.ts_code) AS rn "
                "  FROM market.sector_member m "
                "  LEFT JOIN market.instrument i ON i.ts_code = m.ts_code "
                "  LEFT JOIN market.instrument_daily d "
                "    ON d.ts_code = m.ts_code AND d.trade_date = %s "
                "  WHERE m.source = 'dc' AND m.sector_code = ANY(%s)"
                ") t WHERE rn <= %s ORDER BY sector_code, rn",
                (as_of, top_codes, MEMBERS_PER_CONCEPT),
            ).fetchall()

        members_by_code: dict = {}
        totals_by_code: dict = {}
        for s_code, ts_code, name, pct, total in member_rows:
            members_by_code.setdefault(s_code, []).append({
                "ts_code": ts_code,
                "name": name,
                "pct_chg": float(pct) if pct is not None else None,
            })
            totals_by_code[s_code] = int(total)

        items = []
        max_updated = None
        for rank, (code, score, _period_return, g) in enumerate(top, 1):
            day_rows = g[g["trade_date"] == as_of]
            pct_chg = None
            if not day_rows.empty:
                v = day_rows["pct_chg"].iloc[-1]
                pct_chg = float(v) if pd.notna(v) else None
            members = members_by_code.get(code, [])   # SQL 已按 |pct_chg| 降序截断
            member_total = totals_by_code.get(code, 0)
            last_updated = g["updated_at"].dropna().iloc[-1] \
                if pd.notna(g["updated_at"]).any() else None
            if last_updated is not None and (max_updated is None or last_updated > max_updated):
                max_updated = last_updated
            items.append({
                "sector_code": code,
                "sector_name": names.get(code),
                "rank": rank,
                "heat_score": score,
                "pct_chg": pct_chg,
                "member_total": member_total,
                "members": members,
            })
        return as_of, items, "OK", self._freshness(as_of), max_updated

    def _freshness(self, snapshot_date: date) -> str:
        if self._calendar is None:
            return "STALE"  # 无日历：保守标记
        last_trading = self._calendar.last_trading_day(self._clock.now().date())
        return "FRESH" if snapshot_date >= last_trading else "STALE"

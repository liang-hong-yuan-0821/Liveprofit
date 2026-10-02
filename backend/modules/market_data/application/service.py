"""MarketDataService：Bars 查询与热点现场计算（证券市场数据库统一方案 3.3.1）。

- 市场数据读路径唯一连接源 = market_conn（db.instrument.get_connection 工厂）；
  SQLAlchemy 侧无残留读模型（旧 ORM/仓库已随方案 3.6.1 删除）
- freshness_status 与 market_session_status 正交：休市不是错误。
- 热度现场计算（决策 13）：读 market.sector_daily 按 heat_v1 公式现算，
  不落快照表、不进 Redis；公式与 provider 直连版互指同步（polish a）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
import logging

import pandas as pd

from backend.modules.market_data.application.errors import (
    IntervalNotSupportedError,
    MarketAssetNotFoundError,
    RangeTooLargeError,
)
from backend.modules.market_data.application.indicators import (
    BOLL_K,
    BOLL_PERIOD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    MA_PERIODS,
    compute_indicators,
)
from backend.shared.clock import Clock, SystemClock
from backend.modules.market_data.application.refresh_policy import KLINE_FACTOR_COLUMNS, RefreshPolicy

logger = logging.getLogger(__name__)

# 技术指标不自算（技术指标数据源切换方案 §3.3）：指数/个股主路径指标值取自
# idx_factor_pro/stk_factor_pro 入库数据（上游用区间前历史计算，因子行自带全历史
# 窗口），无需补窗口。显式例外（板块概念Treemap方案 m7，用户拍板 2026-09-16）：
# ① 概念（板块指数）无任何上游因子源 → get_sector_bars 自算（indicators.py）；
# ② 个股因子缓存未完整覆盖请求区间 → 按需拉取 stk_factor_pro 入库补齐
#   （factor_daily 即缓存层，_factor_rows_cover 判定覆盖）。
# 概念自算预热窗口（自然日，口径沿用归档 K线指标叠加方案 3.1）：
# MA60 需 59 个前导交易日 ≈ 88 自然日，另留长假休市余量取 120；板块库内历史短
# （首跑 ~33 根），预热取到多少算多少——历史不足处指标为 None（数据缺失是事实）。
WARMUP_DAYS = 120

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

# 趋势对比组语义常量（趋势对比面板方案 4.2.1：服务端硬编码，前端不持有名单——
# 沿用目录写死先例）。名称与 INDEX_TARGETS 同串；响应只回 name，前端不硬编码。
CAP_TIER_INDEXES: list[tuple[str, str]] = [
    ("000300.SH", "沪深300"), ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"), ("932000.CSI", "中证2000"),
]
BOARD_INDEXES: list[tuple[str, str]] = [
    ("000001.SH", "上证综指"), ("399006.SZ", "创业板指"), ("000688.SH", "科创50"),
]

def _factor_rows_cover(fdf: pd.DataFrame, expected_dates: set[str]) -> bool:
    """个股因子缓存是否逐日覆盖本次实际展示的 bars。

    每日增量采集只写最新 1~2 天个股因子：仅判 empty 会让残段挡住历史补拉——
    指标以残段对齐 bars、其余全 None（2026-09-21 买点卡无均线实况）。
    请求端点是否交易日、股票何时上市均已反映在 bars 日期集合中，无需再猜自然日
    容差或 list_date；区间内部缺任意交易日也会返回 False。
    """
    if not expected_dates:
        return True
    if fdf.empty or "trade_date" not in fdf.columns:
        return False
    rows = fdf.copy()
    rows["_date_key"] = rows["trade_date"].map(
        lambda value: pd.Timestamp(value).date().isoformat()
    )
    requested = rows[rows["_date_key"].isin(expected_dates)]
    factor_dates = set(requested["_date_key"])
    if not expected_dates.issubset(factor_dates):
        return False
    # 每日增量路径可能只写 qfq 列：虽然同日 factor 行存在，K 线使用的 bfq
    # 指标却全部为空。每个展示日至少要有一个可画的 bfq 值，否则触发按需补拉。
    available = [c for c in KLINE_FACTOR_COLUMNS if c in requested.columns]
    if not available:
        return False
    return bool(requested.groupby("_date_key")[available].apply(
        lambda group: group.notna().any(axis=None)
    ).all())


@dataclass(frozen=True)
class AssetDTO:
    """Bars 响应资产信息（裁剪定稿：market/symbol/name 三字段，R1 minor 2）。"""

    market: str
    symbol: str
    name: str


@dataclass(frozen=True)
class TrendSeries:
    """单序列趋势点集（趋势对比面板方案 4.2）：points 按 date 升序。"""

    symbol: str
    name: str
    points: list[dict]      # [{"date": date, "close": float}]，区间无行 → 空列表


@dataclass(frozen=True)
class TrendsDTO:
    """多指数趋势读模型：as_of = 全组末点最大值（全历史口径，与 get_bars latest 同款）。"""

    from_date: date         # 请求区间回显（_trends_data 映射到 TrendsData.from_/to）
    to_date: date
    series: list[TrendSeries]
    as_of: date | None
    freshness_status: str   # FRESH/STALE/UNAVAILABLE


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


@dataclass(frozen=True)
class ConceptResult:
    as_of: date | None
    requested_as_of: date | None
    date_mode: str
    items: list[dict]
    result_status: str
    freshness_status: str
    source_updated_at: object
    coverage: dict


def default_stock_factor_fetcher(symbol: str, start: str, end: str):
    """个股因子按需拉取生产默认（m7）：AI 数据流接口层（provider 单例，按
    LIVEPROFIT_DATA_SOURCE 选源，AKShare 源下基类默认返回 None）。失败返回 None
    → 调用方降级纯 K 线（indicators=None，拉取失败不阻断 K 线响应）。"""
    from AI.dataflows.interface import get_stock_factor_df

    return get_stock_factor_df(symbol, start, end)


class MarketDataService:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        calendar=None,
        market_conn=None,
        stock_factor_fetcher: Callable[[str, str, str], object] | None = None,
        factor_gate: Callable[[str, str, str], bool] | None = None,
        factor_changed: Callable[[str], None] | None = None,
        publish_lag_seconds=None,
    ) -> None:
        """market_conn = db.instrument.get_connection 工厂（contextmanager）。

        uow 参数已删除（决策 13）：热点快照表整链删除后 SQLAlchemy 侧无读模型，
        market_conn 成为市场数据读路径唯一连接源。

        stock_factor_fetcher(symbol, start_iso, end_iso) → stk_factor_pro 帧
        （DataFrame 或 None，失败返回 None）——个股因子按需拉取（m7）；None = 生产
        默认（AI 数据流接口层 provider 单例），测试注入 fake。
        """
        self._clock = clock or SystemClock()
        self._calendar = calendar
        self._market_conn = market_conn
        self._stock_factor_fetcher = stock_factor_fetcher
        self._factor_gate = factor_gate
        self._factor_changed = factor_changed
        self._refresh_policy = RefreshPolicy(calendar=calendar, publish_lag_seconds=publish_lag_seconds)

    # ---- K 线 ----

    def get_bars(
        self, *, market: str, symbol: str, interval: str, from_date: date, to_date: date,
        factor_policy: str = "ensure",
    ) -> BarsDTO:
        # from>to 校验保留（K 线方案把原"超 365 天"用例替换为 from>to——
        # 365 上限已由该方案移除，本方案保留 from>to 拒绝语义）
        if from_date > to_date:
            raise RangeTooLargeError("查询范围无效：开始日期晚于结束日期")
        if interval != "1d":
            raise IntervalNotSupportedError(f"该资产不支持周期：{interval}")
        if factor_policy not in {"ensure", "cache_only"}:
            raise ValueError("factor_policy must be ensure or cache_only")

        # market 形参保留仅契约兼容（回显），不参与查询条件——instrument 无 market 列
        # （决策 9②）；资产存在性 = instrument 查询（instrument_type ∈ index/stock——
        # 板块概念Treemap方案 3.3 放宽个股，fund 仍 404）
        from db.instrument.dao.instrument import get_instrument
        from backend.modules.market_data.infrastructure.refresh_repository import _valid_sql
        expected_date = self._last_trading_day(market)
        target_available = False
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
                target_available = conn.execute(
                    f"SELECT EXISTS(SELECT 1 FROM market.instrument_daily d WHERE d.ts_code=%s "
                    f"AND d.trade_date=%s AND {_valid_sql(['open', 'high', 'low', 'close'])})",
                    (symbol, expected_date),
                ).fetchone()[0]

        if inst is None:
            raise MarketAssetNotFoundError(f"资产不存在：{market} {symbol}")
        # 非 index/stock 类型（如基金代码误传入）同样 404 语义
        if inst["instrument_type"] not in ("index", "stock"):
            raise MarketAssetNotFoundError(f"资产不存在：{market} {symbol}")

        freshness = "UNAVAILABLE"
        if latest is not None:
            freshness = "FRESH" if target_available else "STALE"

        session_status, closed_reason = self._session_status(market)

        bar_dicts = []
        if bars_full is not None and not bars_full.empty:
            bar_dicts = [
                {
                    "timestamp": row["trade_date"],
                    "open": float(row["open"]) if pd.notna(row["open"]) else None,
                    "high": float(row["high"]) if pd.notna(row["high"]) else None,
                    "low": float(row["low"]) if pd.notna(row["low"]) else None,
                    "close": float(row["close"]) if pd.notna(row["close"]) else None,
                    "pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else None,
                    "volume": float(row["vol"]) if pd.notna(row["vol"]) else None,
                }
                for _, row in bars_full.iterrows()
                if all(pd.notna(row[column]) for column in ("open", "high", "low", "close"))
            ]
        indicators = None
        if bar_dicts:
            with self._market_conn() as conn:
                from db.instrument.dao.factor_daily import query_range as factors_range
                fdf = factors_range(conn, symbol, from_date.isoformat(), to_date.isoformat())
            # 个股因子缓存未完整覆盖请求区间 → 按需拉取 stk_factor_pro 并入库缓存
            # （板块概念Treemap方案 m7，用户拍板 2026-09-16：个股接因子、概念自算）。
            # 仅判 fdf.empty 会被每日增量采集写的最新 1~2 天个股因子挡住历史补拉——
            # 指标以残段对齐 bars、其余全 None，图上无均线（2026-09-21 买点卡实况）。
            # 拉取失败/无数据 → indicators=None 纯 K 线（原 3.3 语义，降级不阻断 K 线）；
            # 指数路径保持全 null 数组降级语义（契约测试 test_market_data.py:77-81
            # 冻结断言，不得全局改 None）
            if factor_policy == "ensure" and inst["instrument_type"] == "stock" and not _factor_rows_cover(
                fdf,
                {str(bar["timestamp"])[:10] for bar in bar_dicts},
            ):
                fdf = self._fetch_stock_factors_into_table(symbol, from_date, to_date, cached=fdf)
            # 个股拉取后仍无行 → 短路 indicators=None 纯 K 线；指数路径不短路——
            # 空因子表仍产全 null 数组指标（冻结用例固化语义，不得全局改 None）
            if not (inst["instrument_type"] == "stock" and fdf.empty):
                factor_map = {r["trade_date"]: r for _, r in fdf.iterrows()}

                def _factor_col(name: str) -> list[float | None]:
                    # 判值不判行：行存在但列为 NULL 与行缺失同为 None，不抛异常
                    values: list[float | None] = []
                    for b in bar_dicts:
                        row = factor_map.get(b["timestamp"])
                        value = row[name] if row is not None else None
                        values.append(float(value) if pd.notna(value) else None)
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

    def get_index_trends(self, *, indexes, from_date: date, to_date: date) -> TrendsDTO:
        """多指数趋势读模型（趋势对比面板方案 4.2）：按传入指数清单逐序列区间查询。

        - from>to → RangeTooLargeError（与 get_bars 同语义）
        - 空窗口契约（与 get_bars 一致，不新增分支语义）：区间内无行 → 该序列
          points: []（序列条目仍返回）；全部序列皆空 → series 仍含全部条目、
          as_of 取全历史口径（可能非 None）。不抛错、不 404
        - 不返回 source/source_updated_at：多资产聚合读模型逐序列 provenance
          无消费方（两组合计 7 个指数同为 tushare，前端图注用固定文案）
        """
        if from_date > to_date:
            raise RangeTooLargeError("查询范围无效：开始日期晚于结束日期")
        from db.instrument.dao import instrument_daily
        from backend.modules.market_data.infrastructure.refresh_repository import _valid_sql
        codes = [symbol for symbol, _name in indexes]
        expected_date = self._last_trading_day()
        with self._market_conn() as conn:
            series = []
            for symbol, name in indexes:
                df = instrument_daily.query_range(
                    conn, symbol, from_date.isoformat(), to_date.isoformat())
                points = [
                    {"date": row["trade_date"], "close": float(row["close"])}
                    for _, row in df.iterrows()
                    if pd.notna(row["close"])   # close NaN 的点跳过（DAO 写入已清洗，防御性保留）
                ]
                series.append(TrendSeries(symbol=symbol, name=name, points=points))
            rows = conn.execute(
                "SELECT ts_code, max(trade_date) FROM market.instrument_daily "
                "WHERE ts_code = ANY(%s) GROUP BY ts_code",
                (codes,),
            ).fetchall()
            covered = conn.execute(
                f"SELECT count(DISTINCT d.ts_code) FROM market.instrument_daily d "
                f"WHERE d.ts_code=ANY(%s) AND d.trade_date=%s AND {_valid_sql(['open','high','low','close'])}",
                (codes, expected_date),
            ).fetchone()[0]
        as_of = max((r[1] for r in rows if r[1] is not None), default=None)
        freshness = "UNAVAILABLE"
        if as_of is not None:
            # A latest point in one series cannot cover missing peer indexes.
            freshness = "FRESH" if expected_date is not None and covered == len(set(codes)) else "STALE"
        return TrendsDTO(
            from_date=from_date, to_date=to_date, series=series,
            as_of=as_of, freshness_status=freshness,
        )

    def _fetch_stock_factors_into_table(
        self, symbol: str, from_date: date, to_date: date, *, cached: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Interactive bfq fill: same-connection PG lock, then Redis cooldown.

        Failures retain cached observations and the cooldown. Automatic reads
        never enter this method, and no empty qfq columns are manufactured.
        """
        from db.instrument.dao.factor_daily import upsert_observed_bfq_factors, query_range
        from db.instrument.ingest.guard import IngestGuard, IngestBusy

        fallback = cached if cached is not None else pd.DataFrame()
        if self._factor_gate is None:
            return fallback
        fetcher = self._stock_factor_fetcher or default_stock_factor_fetcher
        try:
            with self._market_conn() as conn:
                with IngestGuard(conn, changed=self._factor_changed) as guard:
                    if not self._factor_gate(symbol, from_date.isoformat(), to_date.isoformat()):
                        return fallback
                    guard.assert_alive()
                    df = fetcher(symbol, from_date.isoformat(), to_date.isoformat())
                    if df is None or df.empty or "trade_date" not in df.columns:
                        return fallback
                    df = df.copy()
                    if "ts_code" in df.columns:
                        df = df[df["ts_code"] == symbol].copy()
                    dates = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
                    df = df[dates.notna() & (dates >= from_date) & (dates <= to_date)].copy()
                    if df.empty:
                        return fallback
                    df["trade_date"] = dates.loc[df.index]
                    df["ts_code"] = symbol
                    df["updated_at"] = self._clock.now()
                    guard.assert_alive()
                    written = upsert_observed_bfq_factors(guard.connection, df)
                    if written:
                        guard.commit("CN_STOCK_DAILY")
                    return query_range(conn, symbol, from_date.isoformat(), to_date.isoformat())
        except IngestBusy:
            return fallback
        except Exception:
            logger.warning("Stock factor refresh unavailable symbol=%s; retaining cache", symbol, exc_info=True)
            return fallback

    def get_sector_bars(
        self, *, market: str, source: str, sector_code: str,
        from_date: date, to_date: date,
    ) -> BarsDTO:
        """概念 K 线（板块概念Treemap方案 3.3 + m7 修订）：读 market.sector_daily。

        - from>to → RangeTooLargeError（同 get_bars）；板块不存在 → 404
        - OHLC 任一 NaN 整行不产出 bar（BarDTO OHLC 为必填 float，None 化会使
          响应 500；close 列 NOT NULL 已由 DAO 保障，本防御只拦脏行）
        - 指标自算（m7 用户拍板 2026-09-16）：板块指数无上游因子源，唯一出路是
          自算——预热取 [from−WARMUP_DAYS, to] 全段计算后切回 [from, to]（板块
          库内历史短，预热取到多少算多少；历史不足处指标为 None，数据缺失是事实）
        """
        if from_date > to_date:
            raise RangeTooLargeError("查询范围无效：开始日期晚于结束日期")
        from backend.modules.market_data.infrastructure.refresh_repository import _valid_sql
        expected_date = self._last_trading_day(market)
        target_available = False
        with self._market_conn() as conn:
            sector = conn.execute(
                "SELECT name FROM market.sector WHERE source = %s AND sector_code = %s",
                (source, sector_code),
            ).fetchone()
            bars_full = None
            warmup_bars = None
            latest = None
            source_updated = None
            if sector is not None:
                from db.instrument.dao.sector_daily import query_bars
                bars_full = query_bars(conn, source, sector_code,
                                       from_date.isoformat(), to_date.isoformat())
                # 指标自算预热帧（m7）：与 bars_full 同过滤口径（OHLC 脏行排除），
                # bar_dicts 是预热有效序列的后缀 → 指标数组切 [-len(bar_dicts):] 即对齐
                warmup_bars = query_bars(
                    conn, source, sector_code,
                    (from_date - timedelta(days=WARMUP_DAYS)).isoformat(),
                    to_date.isoformat(),
                )
                latest_row = conn.execute(
                    "SELECT max(trade_date), max(updated_at) "
                    "FROM market.sector_daily WHERE source = %s AND sector_code = %s",
                    (source, sector_code),
                ).fetchone()
                latest = latest_row[0] if latest_row else None
                source_updated = latest_row[1] if latest_row else None
                target_available = conn.execute(
                    f"SELECT EXISTS(SELECT 1 FROM market.sector_daily d WHERE d.source=%s AND d.sector_code=%s "
                    f"AND d.trade_date=%s AND {_valid_sql(['open','high','low','close'])})",
                    (source, sector_code, expected_date),
                ).fetchone()[0]

        if sector is None:
            raise MarketAssetNotFoundError(f"资产不存在：{market} {sector_code}")

        freshness = "UNAVAILABLE"
        if latest is not None:
            freshness = "FRESH" if target_available else "STALE"

        session_status, closed_reason = self._session_status(market)

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
                    "pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else None,
                    "volume": float(row["vol"]) if pd.notna(row["vol"]) else None,
                })
        indicators = self._compute_sector_indicators(warmup_bars, len(bar_dicts))
        return BarsDTO(
            asset=AssetDTO(market=market, symbol=sector_code, name=sector[0]),
            interval="1d",
            from_date=from_date,
            to_date=to_date,
            bars=bar_dicts,
            indicators=indicators,
            source=source,
            as_of=latest,
            source_updated_at=source_updated,
            freshness_status=freshness,
            market_session_status=session_status,
            market_closed_reason=closed_reason,
        )

    def _compute_sector_indicators(
        self, warmup_bars: pd.DataFrame | None, bar_count: int
    ) -> dict | None:
        """概念指标自算（m7）：预热帧有效行 closes → compute_indicators →
        各数组切回请求窗口（后缀 bar_count 根）。bars 空 → None（契约）。

        有效行 = OHLC 均非 NaN（与 bar_dicts 同过滤口径，保证后缀对齐）。
        """
        if bar_count == 0 or warmup_bars is None or warmup_bars.empty:
            return None
        closes = [
            float(row["close"])
            for _, row in warmup_bars.iterrows()
            if not any(pd.isna(row[c]) for c in ("open", "high", "low", "close"))
        ]
        computed = compute_indicators(closes)
        trim = lambda values: values[-bar_count:]
        return {
            "ma": [
                {"period": line["period"], "values": trim(line["values"])}
                for line in computed["ma"]
            ],
            "boll": {
                "period": BOLL_PERIOD,
                "k": BOLL_K,
                "mid": trim(computed["boll"]["mid"]),
                "upper": trim(computed["boll"]["upper"]),
                "lower": trim(computed["boll"]["lower"]),
            },
            "macd": {
                "fast": MACD_FAST,
                "slow": MACD_SLOW,
                "signal": MACD_SIGNAL,
                "dif": trim(computed["macd"]["dif"]),
                "dea": trim(computed["macd"]["dea"]),
                "hist": trim(computed["macd"]["hist"]),
            },
        }

    def _last_trading_day(self, market: str = "CN") -> date | None:
        if market not in {"CN", "US", "KR"}:
            return None
        return self._refresh_policy.target(f"{market}_INDEX_BARS", self._clock.now()).expected_trade_date

    def _session_status(self, market: str = "CN") -> tuple[str, str | None]:
        if market not in {"CN", "US", "KR"}:
            return "UNKNOWN", "交易日历不可用"
        status = self._refresh_policy.session_status(market, self._clock.now())
        reason = {"CLOSED": "休市/已收盘", "BREAK": "盘中休息", "UNKNOWN": "交易日历不可用"}
        return status, reason.get(status)

    def get_hot_concepts(self, *, market: str, as_of: date | None, limit: int) -> ConceptResult:
        return self._concept_data(market=market, as_of=as_of, limit=limit, include_members=False)

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

    def get_concept_tree(self, *, market: str, as_of: date | None, limit: int) -> ConceptResult:
        return self._concept_data(market=market, as_of=as_of, limit=limit, include_members=True)

    def _concept_data(self, *, market: str, as_of: date | None, limit: int, include_members: bool) -> ConceptResult:
        """Rank only boards with valid quotes on the exact effective date."""
        from backend.modules.market_data.infrastructure.refresh_repository import RefreshRepository, _valid_sql
        from db.instrument.dao.sector_daily import query_by_window

        requested = as_of
        mode = "HISTORICAL" if requested is not None else "LATEST"
        empty_counts = lambda: dict(expected_count=0, available_count=0, exempt_count=0, missing_count=0)
        coverage = {"boards": empty_counts(), "members": empty_counts() if include_members else None}
        def result(items=None, updated=None):
            freshness = "UNAVAILABLE"
            if items:
                freshness = self._freshness(as_of) if not coverage["boards"]["missing_count"] else "STALE"
            return ConceptResult(as_of, requested, mode, items or [], "OK" if items else "NO_HOT_CONCEPTS",
                                 freshness, updated, coverage)
        if market != "CN":
            return result()
        with self._market_conn() as conn:
            names = dict(conn.execute("SELECT sector_code,name FROM market.sector WHERE source='dc'").fetchall())
            coverage["boards"]["expected_count"] = len(names)
            coverage["boards"]["missing_count"] = len(names)
            if as_of is None:
                as_of = RefreshRepository().concept_display_date(conn=conn)
            if as_of is None:
                return result()
            candidates = [row[0] for row in conn.execute(
                f"SELECT d.sector_code FROM market.sector_daily d WHERE d.source='dc' AND d.trade_date=%s "
                f"AND d.sector_code=ANY(%s) AND {_valid_sql(['open', 'high', 'low', 'close', 'pct_chg'])}",
                (as_of, list(names)),
            ).fetchall()]
            coverage["boards"]["available_count"] = len(candidates)
            coverage["boards"]["missing_count"] = len(names) - len(candidates)
            if not candidates:
                return result()
            dates = conn.execute(
                "SELECT DISTINCT trade_date FROM market.sector_daily WHERE source='dc' AND trade_date<=%s "
                "AND sector_code=ANY(%s) ORDER BY trade_date DESC LIMIT %s",
                (as_of, candidates, HEAT_QUERY_DAYS),
            ).fetchall()
            frame = query_by_window(conn, "dc", dates[-1][0].isoformat(), as_of.isoformat())
            frame = frame[frame["sector_code"].isin(candidates)]
            top = self._compute_sector_heat(frame)[:limit]
            members_by_code, totals_by_code = {}, {}
            if include_members and top:
                top_codes = [code for code, *_ in top]
                member_rows = conn.execute(
                    "WITH classified AS (SELECT m.sector_code,m.ts_code,COALESCE(i.name,m.ts_code) AS name,"
                    " d.pct_chg, CASE "
                    "WHEN i.list_date>%s OR i.delist_date<=%s "
                    " OR (i.list_status IN ('G','U') AND i.list_date IS NULL) THEN 'EXCLUDED' "
                    "WHEN i.list_date IS NULL OR i.list_status IS NULL OR i.list_status NOT IN ('L','P','D','G','U') "
                    " OR (i.list_status='D' AND i.delist_date IS NULL) THEN 'MISSING' "
                    f"WHEN {_valid_sql(['open', 'high', 'low', 'close', 'pct_chg'])} THEN 'AVAILABLE' "
                    "WHEN t.is_suspended AND t.suspension_scope='full_day' "
                    "AND t.source IN ('tushare','tushare+baostock') "
                    "THEN 'EXEMPT' ELSE 'MISSING' END AS state "
                    "FROM (SELECT DISTINCT sector_code,ts_code FROM market.sector_member "
                    "WHERE source='dc' AND sector_code=ANY(%s)) m "
                    "LEFT JOIN market.instrument i ON i.ts_code=m.ts_code AND i.instrument_type='stock' "
                    "LEFT JOIN market.instrument_daily d ON d.ts_code=m.ts_code AND d.trade_date=%s "
                    "LEFT JOIN market.trade_status_effective t ON t.ts_code=m.ts_code AND t.trade_date=%s), "
                    "ranked AS (SELECT *,count(*) OVER w AS member_total, "
                    "count(*) FILTER(WHERE state!='EXCLUDED') OVER w AS expected_count, "
                    "count(*) FILTER(WHERE state='AVAILABLE') OVER w AS available_count, "
                    "count(*) FILTER(WHERE state='EXEMPT') OVER w AS exempt_count, "
                    "count(*) FILTER(WHERE state='MISSING') OVER w AS missing_count, "
                    "row_number() OVER(PARTITION BY sector_code ORDER BY abs(pct_chg) DESC NULLS LAST,ts_code) AS rn "
                    "FROM classified WINDOW w AS (PARTITION BY sector_code)) "
                    "SELECT sector_code,ts_code,name,pct_chg,member_total,expected_count,available_count,exempt_count,missing_count "
                    "FROM ranked WHERE rn<=%s ORDER BY sector_code,rn",
                    (as_of, as_of, top_codes, as_of, as_of, MEMBERS_PER_CONCEPT),
                ).fetchall()
                for code, symbol, name, pct, total, expected, available, exempt, missing in member_rows:
                    members_by_code.setdefault(code, []).append({"ts_code": symbol, "name": name,
                                                               "pct_chg": float(pct) if pd.notna(pct) else None})
                    if code not in totals_by_code:
                        totals_by_code[code] = total
                        for key, value in zip(coverage["members"], (expected, available, exempt, missing)):
                            coverage["members"][key] += value
        items, max_updated = [], None
        for rank, (code, score, period_return, group) in enumerate(top, 1):
            timestamps = group["updated_at"].dropna()
            updated = timestamps.max() if not timestamps.empty else None
            if updated is not None and (max_updated is None or updated > max_updated):
                max_updated = updated
            item = dict(sector_code=code, sector_name=names[code], rank=rank, heat_window_rows=len(group))
            if include_members:
                today = group[group["trade_date"] == as_of].iloc[-1]
                item.update(heat_score=score, pct_chg=float(today["pct_chg"]),
                            members=members_by_code.get(code, []), member_total=totals_by_code.get(code, 0))
            else:
                item.update(hotness_reason=None, period_return=period_return, updated_at=updated, bars=[],
                            daily_changes=[{"date": row["trade_date"], "change_pct": float(row["pct_chg"])}
                                           for _, row in group.tail(HEAT_WINDOW_DAYS).iterrows() if pd.notna(row["pct_chg"])])
            items.append(item)
        return result(items, max_updated)

    def _freshness(self, snapshot_date: date | None, market: str = "CN") -> str:
        if snapshot_date is None:
            return "UNAVAILABLE"
        expected = self._last_trading_day(market)
        return "FRESH" if expected is not None and snapshot_date >= expected else "STALE"

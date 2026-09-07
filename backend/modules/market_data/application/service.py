"""MarketDataService：资产目录、Bars、热点快照查询与新鲜度判定（§2.6.1 / §3.1.5）。

- 只消费规范化读模型（DataFrame→显式 mapper→规范化表），禁止解析 Markdown。
- freshness_status 与 market_session_status 正交：休市不是错误。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from backend.modules.market_data.application.errors import (
    AssetDisabledError,
    HotConceptsUpstreamUnavailableError,
    IntervalNotSupportedError,
    MarketAssetNotFoundError,
    MarketDataUpstreamUnavailableError,
    RangeTooLargeError,
)
from backend.modules.market_data.infrastructure.repositories import (
    ConceptSnapshotRepository,
    MarketAssetRepository,
    MarketBarRepository,
)
from backend.modules.market_data.application.indicators import compute_indicators
from backend.shared.clock import Clock, SystemClock

MAX_RANGE_DAYS = 365
# 指标补窗口：MA60 需 59 个前导交易日 ≈ 88 自然日，另留长假休市余量（春节前后
# 90 自然日内可休 7-9 个交易日，最坏前导 <59），取 120 保证前导始终 ≥59（K线指标叠加方案 §3.1）
WARMUP_DAYS = 120


@dataclass(frozen=True)
class AssetDTO:
    market: str
    symbol: str
    name: str
    currency: str
    market_timezone: str
    display_order: int
    enabled: bool
    supported_intervals: list
    availability_status: str


@dataclass(frozen=True)
class BarsDTO:
    asset: AssetDTO
    interval: str
    from_date: date
    to_date: date
    bars: list[dict]
    indicators: dict | None  # MA/BOLL 并列数组，与 bars 等长按 index 对齐；bars 空时 None
    source: str | None
    as_of: date | None
    source_updated_at: object
    freshness_status: str  # FRESH/STALE/UNAVAILABLE
    market_session_status: str  # OPEN/CLOSED
    market_closed_reason: str | None


class MarketDataService:
    def __init__(
        self,
        uow,
        *,
        clock: Clock | None = None,
        calendar=None,
    ) -> None:
        self._uow = uow
        self._assets = MarketAssetRepository(uow.session)
        self._bars = MarketBarRepository(uow.session)
        self._concepts = ConceptSnapshotRepository(uow.session)
        self._clock = clock or SystemClock()
        self._calendar = calendar

    # ---- 资产目录 ----

    def list_assets(self, *, enabled_only: bool = True) -> list[AssetDTO]:
        rows = self._assets.list_enabled() if enabled_only else self._assets.list_all()
        return [self._to_asset_dto(asset) for asset in rows]

    # ---- K 线 ----

    def get_bars(
        self, *, market: str, symbol: str, interval: str, from_date: date, to_date: date
    ) -> BarsDTO:
        if from_date > to_date or (to_date - from_date).days > MAX_RANGE_DAYS:
            raise RangeTooLargeError(f"查询范围超过上限（{MAX_RANGE_DAYS} 自然日）")
        asset = self._assets.get_by_symbol(market, symbol)
        if asset is None:
            raise MarketAssetNotFoundError(f"资产不存在：{market} {symbol}")
        if not asset.enabled:
            raise AssetDisabledError(f"资产未启用：{market} {symbol}")
        if interval not in (asset.supported_intervals or []):
            raise IntervalNotSupportedError(f"该资产不支持周期：{interval}")
        if asset.availability_status == "UNAVAILABLE":
            raise MarketDataUpstreamUnavailableError(
                f"资产未通过实测验收，无可用数据：{market} {symbol}"
            )

        # 补窗口读取：多取 WARMUP_DAYS 自然日用于 MA/BOLL 计算，返回仍按 [from, to] 切片
        bars_full = self._bars.list_range(asset.id, from_date - timedelta(days=WARMUP_DAYS), to_date)
        latest = self._bars.latest_date(asset.id)
        source_updated = self._bars.latest_source_updated(asset.id)
        freshness = "UNAVAILABLE"
        if latest is not None:
            last_trading = self._last_trading_day()
            freshness = "FRESH" if last_trading is not None and latest >= last_trading else "STALE"

        session_status, closed_reason = self._session_status()

        bar_dicts = [
            {
                "timestamp": bar.trading_date,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume) if bar.volume is not None else None,
            }
            for bar in bars_full
        ]
        indicators = None
        if bar_dicts:
            computed = compute_indicators([bar["close"] for bar in bar_dicts])
            # 等 index 切片：指标数组与 bar_dicts 对齐后一起截断到 [from, to]
            start = next(
                (i for i, bar in enumerate(bar_dicts) if bar["timestamp"] >= from_date), None
            )
            if start is None:
                bar_dicts = []
            else:
                bar_dicts = bar_dicts[start:]
                indicators = {
                    "ma": [
                        {"period": line["period"], "values": line["values"][start:]}
                        for line in computed["ma"]
                    ],
                    "boll": {
                        "period": computed["boll"]["period"],
                        "k": computed["boll"]["k"],
                        "mid": computed["boll"]["mid"][start:],
                        "upper": computed["boll"]["upper"][start:],
                        "lower": computed["boll"]["lower"][start:],
                    },
                }
        return BarsDTO(
            asset=self._to_asset_dto(asset),
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            bars=bar_dicts,
            indicators=indicators,
            source=asset.data_source,
            as_of=latest,
            source_updated_at=source_updated,  # 入库采集时间（datetime），与 as_of 数据时点区分
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

    # ---- 热点快照 ----

    def get_hot_concepts(
        self, *, market: str, as_of: date | None, limit: int
    ) -> tuple[date | None, list[dict], str, str]:
        """读取最新已完成快照（as_of 省略时）。

        - 首期仅 CN：其他市场返回上游不可用语义（503，由错误映射处理）。
        - 无热点为正常业务态（NO_HOT_CONCEPTS，200 空 items）。
        - freshness：快照日期覆盖最近已收盘交易日 → FRESH，否则 STALE。
        """
        if market != "CN":
            raise HotConceptsUpstreamUnavailableError(f"热门概念首期仅支持 CN：{market}")
        snapshot_date = as_of or self._concepts.latest_as_of(market)
        if snapshot_date is None:
            return None, [], "NO_HOT_CONCEPTS", "STALE"
        rows = self._concepts.list_by_date(market, snapshot_date, limit=limit)
        if not rows:
            return snapshot_date, [], "NO_HOT_CONCEPTS", self._snapshot_freshness(snapshot_date)
        items = [
            {
                "concept_code": row.concept_code,
                "concept_name": row.concept_name,
                "rank": row.rank,
                "hotness_reason": row.hotness_reason,
                "period_return": float(row.period_return) if row.period_return is not None else None,
                "daily_changes": row.daily_changes,
                "updated_at": row.source_updated_at or row.created_at,
                "bars": [],  # 首版无概念 K 线读模型：卡片显示不可用状态（契约允许）
            }
            for row in rows
        ]
        return snapshot_date, items, "OK", self._snapshot_freshness(snapshot_date)

    def _snapshot_freshness(self, snapshot_date: date) -> str:
        if self._calendar is None:
            return "STALE"  # 无日历：保守标记，前端展示快照日期与延迟提示
        last_trading = self._calendar.last_trading_day(self._clock.now().date())
        return "FRESH" if snapshot_date >= last_trading else "STALE"

    # ---- 投影 ----

    @staticmethod
    def _to_asset_dto(asset) -> AssetDTO:
        return AssetDTO(
            market=asset.market,
            symbol=asset.symbol,
            name=asset.name,
            currency=asset.currency,
            market_timezone=asset.market_timezone,
            display_order=asset.display_order,
            enabled=asset.enabled,
            supported_intervals=asset.supported_intervals or [],
            availability_status=asset.availability_status,
        )

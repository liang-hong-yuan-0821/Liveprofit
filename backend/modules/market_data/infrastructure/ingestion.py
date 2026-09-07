"""行情/热点 ingestion（§3.1.5：DataFrame→显式 mapper→规范化表，禁止解析 Markdown）。

- CN 指数日线：Tushare get_index_data_df（结构化 DataFrame，列 trade_date/open/high/low/close/vol/amount）。
- US/KR：AKShare candidate，仅实测验收通过的资产才能 AVAILABLE；ingest 入口保留 live 标记。
- CN 热点快照（heat_v1）：TODO 占位——量变矩阵（算法 0.4 权重）尚未确认结构化来源，
  在确认前不落快照（避免伪造 heat_v1 结果），服务端返回 NO_HOT_CONCEPTS。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from backend.modules.market_data.infrastructure.repositories import (
    ConceptSnapshotRepository,
    MarketAssetRepository,
    MarketBarRepository,
)

logger = logging.getLogger(__name__)


class IndexBarsIngestion:
    """指数日线 ingestion：只消费 DataFrame→显式 mapper→规范化表。"""

    def __init__(self, uow, provider_factory) -> None:
        self._uow = uow
        self._assets = MarketAssetRepository(uow.session)
        self._bars = MarketBarRepository(uow.session)
        self._provider_factory = provider_factory

    def ingest_asset(self, market: str, symbol: str, start: str, end: str) -> int:
        asset = self._assets.get_by_symbol(market, symbol)
        if asset is None:
            raise ValueError(f"资产不在目录：{market} {symbol}")
        if asset.market != "CN":
            raise NotImplementedError(
                f"US/KR ingestion 为 live 验收路径：AKShare candidate 经实测验收后才置 AVAILABLE（{market} {symbol}）"
            )
        provider = self._provider_factory(asset.market)
        frame = provider.get_index_data_df(symbol, start, end)
        if frame is None or getattr(frame, "empty", True):
            raise RuntimeError(f"Provider 返回空数据：{market} {symbol}")

        rows = []
        source_updated_at = datetime.now(timezone.utc)
        for record in frame.to_dict("records"):
            trading_date = _as_date(record.get("trade_date"))
            if trading_date is None:
                continue
            rows.append(
                {
                    "trading_date": trading_date,
                    "open": _as_float(record.get("open")),
                    "high": _as_float(record.get("high")),
                    "low": _as_float(record.get("low")),
                    "close": _as_float(record.get("close")),
                    "volume": _as_float(record.get("vol")),
                    "source_updated_at": source_updated_at,
                }
            )
        valid = [row for row in rows if all(v is not None for v in (row["open"], row["high"], row["low"], row["close"]))]
        if not valid:
            raise RuntimeError(f"规范化后无有效行：{market} {symbol}")
        # 批内重复 trade_date 去重：同一 INSERT 批次两行相同 PK 会触发
        # "ON CONFLICT DO UPDATE cannot affect row a second time"（CLAUDE.md 已记同类坑）
        deduped = {row["trading_date"]: row for row in valid}
        self._bars.upsert(asset.id, list(deduped.values()), source=asset.data_source or "unknown")
        self._uow.commit()
        return len(deduped)


class ConceptSnapshotIngestion:
    """CN 概念热点快照 ingestion。

    TODO（heat_v1 完整算法占位）：热度 = 区间涨跌幅×0.6 + 成交量变化×0.4（P-2 已确认）。
    区间涨跌幅可取自 get_concept_daily_returns_matrix（结构化例外），
    成交量变化矩阵尚无确认的结构化来源 → 在确认前不生成快照，
    避免产出不含量变权重的伪 heat_v1 结果。
    """

    def __init__(self, uow) -> None:
        self._uow = uow
        self._repo = ConceptSnapshotRepository(uow.session)

    def ingest_cn_snapshot(self, as_of: date, top_n: int = 30) -> int:
        raise NotImplementedError(
            "heat_v1 快照生成待量变矩阵结构化来源确认（见模块 docstring TODO）；"
            "确认前服务端返回 NO_HOT_CONCEPTS，不伪造热度"
        )


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

"""市场数据 Schema（§2.6.1：K 线、热点——目录端点已删，前端写死清单，决策 4）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Market = Literal["US", "KR", "CN"]
Freshness = Literal["FRESH", "STALE", "UNAVAILABLE"]
Session = Literal["OPEN", "CLOSED"]


class BarDTO(BaseModel):
    timestamp: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None


class MaLineDTO(BaseModel):
    """单条均线：values 与 bars 等长按 index 对齐，因子行缺失处为 null。"""

    period: int
    values: list[float | None]


class BollBandsDTO(BaseModel):
    """布林带三线：各数组与 bars 等长按 index 对齐，因子行缺失处为 null。"""

    period: int
    k: float
    mid: list[float | None]
    upper: list[float | None]
    lower: list[float | None]


class MacdDTO(BaseModel):
    """MACD 副图（技术指标数据源切换方案 §3.3）：dif/dea/hist 与 bars 等长对齐。

    hist = 上游 macd_bfq 原值（≈2×(dif−dea)，上游口径，不自算）。
    """

    fast: int
    slow: int
    signal: int
    dif: list[float | None]
    dea: list[float | None]
    hist: list[float | None]


class IndicatorsDTO(BaseModel):
    """MA/BOLL/MACD 技术指标（值取自 idx_factor_pro 入库数据，不自算）。

    bars 为空时整个字段为 null；macd 可选——旧后端响应无此字段不破坏解析。
    """

    ma: list[MaLineDTO]
    boll: BollBandsDTO
    macd: MacdDTO | None = None


class BarsAssetInfo(BaseModel):
    """market = 请求 market 形参回显（非表字段，instrument 无 market 列——五轮调整定稿）。"""

    market: Market
    symbol: str
    name: str


class BarsData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    asset: BarsAssetInfo
    interval: str
    from_: date = Field(alias="from")
    to: date
    bars: list[BarDTO]
    indicators: IndicatorsDTO | None = None
    source: str | None
    as_of: date | None
    source_updated_at: datetime | None
    freshness_status: Freshness
    market_session_status: Session
    market_closed_reason: str | None


class DailyChangeDTO(BaseModel):
    date: date
    change_pct: float


class HotConceptDTO(BaseModel):
    """字段随 sector 体系改名（决策 12 连带）：concept_code/concept_name →
    sector_code/sector_name；hotness_reason 现场计算版恒 NULL（LLM 未实现）。"""

    sector_code: str
    sector_name: str
    rank: int
    hotness_reason: str | None
    period_return: float | None
    daily_changes: list[DailyChangeDTO] | None
    updated_at: datetime | None
    bars: list[BarDTO]


class HotConceptsData(BaseModel):
    as_of: date | None
    algorithm_version: str
    result_status: Literal["OK", "NO_HOT_CONCEPTS"]
    items: list[HotConceptDTO]
    source: str | None
    source_updated_at: datetime | None
    freshness_status: Literal["FRESH", "STALE"]


class ConceptMemberDTO(BaseModel):
    """概念成分股（板块概念Treemap方案 3.2）：pct_chg = as_of 当日个股涨跌幅；
    停牌/无行情行为 null（前端置灰）。name 缺失时以 ts_code 兜底（契约 name 必填）。"""

    ts_code: str
    name: str
    pct_chg: float | None


class ConceptTreeNodeDTO(BaseModel):
    """treemap 概念节点：heat_score = 矩形大小、pct_chg = 矩形颜色（as_of 当日
    板块涨跌幅，无行情行 → null）；members = 按 |pct_chg| 降序截断 top 100，
    member_total 标该概念成分全量数。"""

    sector_code: str
    sector_name: str
    rank: int
    heat_score: float
    pct_chg: float | None
    member_total: int
    members: list[ConceptMemberDTO]


class ConceptTreeData(BaseModel):
    as_of: date | None
    algorithm_version: str
    result_status: Literal["OK", "NO_HOT_CONCEPTS"]
    items: list[ConceptTreeNodeDTO]
    source: str | None
    source_updated_at: datetime | None
    freshness_status: Literal["FRESH", "STALE"]

"""event_study 应用契约（Q-05 已确认：冻结既有 8 字段预测契约）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EventStudyPredictionCommand:
    event_text: str
    asset_ticker: str
    window_type: str = "post_event_5d"
    event_type: str | None = None
    event_subtype: str | None = None
    event_condition: str | None = None
    save: bool = False
    event_id: int | None = None


@dataclass(frozen=True)
class PredictionDTO:
    prediction: dict
    template_stats: dict
    supplement_events: list[dict]
    note: str | None


@dataclass(frozen=True)
class EventStudyAssetDTO:
    ticker: str
    name: str
    market: str


@dataclass(frozen=True)
class MacroInformationDTO:
    id: str
    event_id: int | None
    title: str
    occurred_at: Any
    market_tags: list
    macro_topic: str | None
    summary: str | None
    source: str | None
    related_assets: list | None
    research_status: str | None

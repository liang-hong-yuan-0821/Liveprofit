"""事件研究 Schema（Q-05 冻结契约 + 资产清单）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PredictionRequest(BaseModel):
    event_text: str = Field(min_length=1, max_length=20000)
    asset_ticker: str = Field(min_length=1, max_length=32)
    window_type: Literal["pre_event_5d", "event_day", "post_event_5d"] = "post_event_5d"
    event_type: str | None = None
    event_subtype: str | None = None
    event_condition: str | None = None
    save: bool = False
    event_id: int | None = None


class PredictionData(BaseModel):
    prediction: dict[str, Any]
    template_stats: dict[str, Any]
    supplement_events: list[dict[str, Any]]
    note: str | None


class EventStudyAssetDTO(BaseModel):
    ticker: str
    name: str
    market: str


class AssetListData(BaseModel):
    items: list[EventStudyAssetDTO]

"""宏观信息 Schema（§2.6.1：仅 APPROVED 投影，occurred_at DESC）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MacroInformationItemDTO(BaseModel):
    id: str
    event_id: int | None
    title: str
    occurred_at: datetime | None
    market_tags: list[str]
    macro_topic: str | None
    summary: str | None
    source: str | None
    related_assets: list[str] | None
    research_status: str | None


class MacroInformationData(BaseModel):
    items: list[MacroInformationItemDTO]

"""
API Pydantic 模型（方案 3.8）
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    """POST /predict 请求体（3.8.1）。

    三级路由（方案第三章）：`as_of` 防前视截止时点、`event_scope` 同作用域过滤，
    两者均可缺省（缺省语义见字段说明），旧调用方无需改动。
    """

    event_text: str = Field(..., min_length=1, max_length=20000,
                            description="事件文本（标题/摘要）")
    asset_ticker: str = Field(..., description="目标资产代码，如 000001.SH")
    window_type: str = Field(
        "post_event_5d",
        description="预测窗口：pre_event_5d / event_day / post_event_5d",
    )
    event_type: Optional[str] = Field(None, description="事件类型（模板标签，可选）")
    event_subtype: Optional[str] = Field(None, description="事件子类型（模板标签，可选）")
    event_condition: Optional[str] = Field(None, description="关键条件（模板标签，可选）")
    as_of: Optional[str] = Field(
        None,
        description="防前视截止时点（ISO8601，边界包含）；缺省 = 事件自身时间"
                    "（提供 event_id 时取其 announced_at），两者均缺省时不限定时间",
    )
    event_scope: Optional[Literal["market", "sector", "stock"]] = Field(
        None,
        description="三级路由作用域；缺省 = 不限定作用域（兼容旧调用）",
    )
    save: bool = Field(False, description="True 时预测落库 predictions 表（显式保存）")
    event_id: Optional[int] = Field(None, description="关联事件 ID（save=True 时，"
                                                     "同时作为 as_of 缺省来源）")


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    redis: bool

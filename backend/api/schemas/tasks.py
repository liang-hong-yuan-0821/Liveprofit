"""分析任务 Schema（§2.6.2：创建 discriminated union、TaskListItemDTO、TaskDTO）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field

AnalysisLayer = Literal["market", "sector", "stock", "screening", "position"]
TaskStatusValue = Literal[
    "PENDING", "QUEUED", "RUNNING", "RETRYING", "SUCCEEDED", "FAILED", "CANCELLED", "CANCEL_REQUESTED"
]
TaskTypeValue = Literal["SINGLE_STOCK", "MARKET_WIDE"]


class AnalysisOptions(BaseModel):
    """接口定义的其余选项（扩展点，服务层复验）。"""

    model_config = {"extra": "allow"}


class SingleStockCreateRequest(BaseModel):
    task_type: Literal["SINGLE_STOCK"]
    ticker: str = Field(min_length=1, max_length=32, description="单股分析目标")
    requested_trade_date: date
    selected_layers: list[AnalysisLayer] = Field(min_length=1)
    analysis_options: AnalysisOptions | None = None


class MarketWideCreateRequest(BaseModel):
    task_type: Literal["MARKET_WIDE"]
    ticker: None = None  # 全市场禁止 ticker
    requested_trade_date: date
    selected_layers: list[AnalysisLayer] = Field(min_length=1)
    analysis_options: AnalysisOptions | None = None


CreateAnalysisTaskRequest = Annotated[
    Union[SingleStockCreateRequest, MarketWideCreateRequest], Field(discriminator="task_type")
]


class TaskCreatedData(BaseModel):
    task_id: UUID
    status: TaskStatusValue
    requested_trade_date: date | None
    effective_trade_date: date | None
    date_correction: str | None
    events_url: str = Field(description="服务端派生的 SSE 相对 URL")
    report_url: str = Field(description="服务端派生的报告相对 URL")
    execution_logs_url: str = Field(description="服务端派生的执行调用日志相对 URL")
    graph_topology_url: str = Field(description="服务端派生的图拓扑相对 URL")


class TaskListItemDTO(BaseModel):
    id: UUID
    task_type: TaskTypeValue
    ticker: str | None
    effective_trade_date: date | None
    status: TaskStatusValue
    attempt_no: int
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime


class TaskListData(BaseModel):
    items: list[TaskListItemDTO]


class TaskDTO(BaseModel):
    id: UUID
    task_type: TaskTypeValue
    ticker: str | None
    requested_trade_date: date | None
    effective_trade_date: date | None
    date_correction: str | None
    selected_layers: list[str]
    status: TaskStatusValue
    attempt_no: int
    next_retry_at: datetime | None
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime
    events_url: str = Field(description="恒等于 /api/v1/analysis-tasks/{id}/events")
    report_url: str = Field(description="恒等于 /api/v1/analysis-tasks/{id}/report")
    execution_logs_url: str = Field(description="恒等于 /api/v1/analysis-tasks/{id}/execution-logs")
    graph_topology_url: str = Field(description="恒等于 /api/v1/analysis-tasks/{id}/graph-topology")

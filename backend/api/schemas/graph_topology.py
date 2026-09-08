"""图拓扑 Schema（任务拓扑图方案 §2.1）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TopologyNodeStatus = Literal["not_executed", "executed", "running", "error"]
TopologyEdgeKind = Literal["direct", "conditional", "loop"]


class TopologyNodeDTO(BaseModel):
    """拓扑主节点（含未执行节点）。"""

    id: str = Field(description='"{layer}:{label}"，如 "stock:Bull Researcher"')
    label: str = Field(description='图节点显示名（含 " Analyst" 后缀），如 "Bull Researcher"、"Screening"')
    layer: str = Field(description='所属层目录名，如 "market" / "stock"')
    row: int = Field(description="行号（= 层执行序）：market=0、sector=1、stock=2、screening=2；screening 任务中 stock=3")
    order: int = Field(description="层内主节点序（DFS 前序），如 stock 层 Bull Researcher=4")
    status: TopologyNodeStatus = Field(description='未执行/已执行/执行中/出错')
    invocation_count: int = Field(description="匹配到的节点日志目录数（LLM 目录 + 纯代码节点预测目录）")
    dirs: list[str] = Field(description='相对任务目录的节点目录路径，按名（seq）排序，如 ["stock/005_Bull_Researcher"]')


class TopologyEdgeDTO(BaseModel):
    source: str = Field(description='源节点 id，如 "stock:Bull Researcher"')
    target: str = Field(description='目标节点 id，如 "stock:Bear Researcher"')
    kind: TopologyEdgeKind = Field(description="direct/conditional（条件边虚线）/loop（screening→stock 逐票循环虚线）")
    parallel: bool = Field(description="存在反向边（如 Bull↔Bear 互指），前端画弧线避免重叠")


class GraphTopologyDTO(BaseModel):
    task_id: str
    attempt_no: int
    available: bool = Field(description="run 目录是否存在且可读；false 时 nodes 仍为静态结构、status 全 not_executed")
    generated_at: datetime = Field(description="组装时间")
    nodes: list[TopologyNodeDTO]
    edges: list[TopologyEdgeDTO]

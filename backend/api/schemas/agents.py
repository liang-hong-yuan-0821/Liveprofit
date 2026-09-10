"""Agent 提示词管理 Schema（单Agent重跑与提示词编辑方案 3.3）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.api.schemas.graph_topology import TopologyEdgeDTO


class AgentTopologyNodeDTO(BaseModel):
    """静态全局拓扑节点（含提示词可编辑性标注）。"""

    id: str = Field(description='"{layer}:{label}"，如 "market:CN News Analyst"')
    label: str = Field(description='图节点显示名（含 " Analyst" 后缀）')
    layer: str = Field(description="所属层目录名，如 market / sector / stock / screening")
    row: int = Field(description="行号（= 层执行序）")
    order: int = Field(description="层内主节点序")
    has_prompt: bool = Field(description="是否有可编辑提示词（Screening 纯代码节点为 false）")
    has_override: bool = Field(description="是否已自定义提示词（前端标记「已自定义」）")


class AgentTopologyDTO(BaseModel):
    """静态全局拓扑（全层形态，build_topology 单一事实来源）。"""

    nodes: list[AgentTopologyNodeDTO]
    edges: list[TopologyEdgeDTO]
    generated_at: datetime = Field(description="组装时间")


class AgentPromptDTO(BaseModel):
    node_id: str = Field(description='"{layer}:{label}"，如 "market:CN News Analyst"')
    label: str
    layer: str
    default_prompt: str = Field(description="默认提示词（展示版：输出格式已展开、日期行移除）")
    override_prompt: str | None = Field(description="覆盖提示词全文；无覆盖为 null")
    has_override: bool
    updated_at: datetime | None = Field(description="覆盖最近更新时间；无覆盖为 null")


class AgentPromptListData(BaseModel):
    items: list[AgentPromptDTO]


class UpsertAgentPromptRequest(BaseModel):
    prompt_text: str = Field(description="覆盖提示词全文（原样生效，不做插值）；1..20000 字符")

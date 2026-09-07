"""执行调用日志 Schema（任务执行调用日志方案 §3.2.1）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionFileKind = Literal["md", "json", "txt"]


class ExecutionFileDTO(BaseModel):
    """日志内容文件（相对任务目录）。truncated 统一语义：内容未内嵌，可经 content 端点拉全量。"""

    path: str = Field(description="相对任务目录的文件路径")
    kind: ExecutionFileKind = Field(description="按扩展名分流")
    content: str | dict[str, Any] | None = Field(
        default=None, description="kind=md/txt → str；kind=json → 解析后 dict；解析失败/超限截断时 → null")
    parse_error: bool = Field(default=False, description="json 解析失败（content=null 时区分原因）")
    truncated: bool = Field(default=False, description="超过内容上限，content=null")
    total_bytes: int = Field(description="原始文件字节数（截断时前端展示「查看完整内容」）")


class ExecutionTushareDTO(BaseModel):
    """tushare 端点子调用（DP 调用目录 tushare/ 内）。"""

    dir: str
    name: str = Field(description="api_name")
    seq: int | None
    ts: str | None
    probe: bool
    error: bool
    req: dict[str, Any] | None = Field(description="{api_name, fields, params}")
    res: ExecutionFileDTO | None = Field(description="res.json（DataFrame 归一结构或 {\"error\":...}）")


class ExecutionDpCallDTO(BaseModel):
    """dataprovider 调用（新格式目录或旧格式平铺 json）。"""

    dir: str | None = Field(default=None, description="新格式目录相对路径；旧格式为 null")
    name: str = Field(description="接口名（meta.name 或目录/文件名推导）")
    desc: str | None
    seq: int | None
    ts: str | None
    error: bool
    legacy: bool = Field(description="旧格式平铺文件")
    req: dict[str, Any] | None = Field(description="req.json；旧格式 payload.req")
    res: ExecutionFileDTO | None = Field(description="res.md/res.json；旧格式 payload.res（dict → kind=json）")
    tushare: list[ExecutionTushareDTO]


class ExecutionToolDTO(BaseModel):
    """LLM 工具调用。"""

    dir: str
    name: str = Field(description="工具名（req.json 的 name，缺省取目录名）")
    req: dict[str, Any] | None
    res: ExecutionFileDTO | None = Field(description="res.txt")


class ExecutionNodeDTO(BaseModel):
    """LLM 节点目录。"""

    dir: str = Field(description='相对任务目录，如 "sector/001_Sector_News_Analyst"')
    seq: int | None = Field(description="目录名数字前缀；解析失败为 null")
    node: str | None = Field(description="meta.json 的 node（LLM 节点名）")
    model: str | None = Field(description="meta.json 的 model")
    meta: dict[str, Any] | None = Field(description="meta.json 原样（含 tool_calls 摘要）")
    llm_req: ExecutionFileDTO | None = Field(description="req.md")
    llm_res: ExecutionFileDTO | None = Field(description="res.md")
    tools: list[ExecutionToolDTO] = Field(description="tools/ 子目录，按名称排序")
    dp_calls: list[ExecutionDpCallDTO] = Field(description="新格式目录 + 旧格式平铺 json，按名称排序")


class ExecutionLayerDTO(BaseModel):
    name: str = Field(description='"market" / "sector" / "stock" / "screening" / 未知层名')
    nodes: list[ExecutionNodeDTO] = Field(description="按目录名排序（seq 前缀）")


class ExecutionLogsDTO(BaseModel):
    task_id: str
    attempt_no: int
    available: bool = Field(description="任务日志目录是否存在且可读；false 时 layers 为空")
    generated_at: datetime = Field(description="树构建时间")
    layers: list[ExecutionLayerDTO]

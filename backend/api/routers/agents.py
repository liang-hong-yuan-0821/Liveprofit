"""Agent 提示词管理 REST 路由（单Agent重跑与提示词编辑方案 3.3）。

- 静态全局拓扑：AI.graph.topology.build_topology 单一事实来源（延迟导入，
  与 graph_topology 路由同款先例）；has_override 由覆盖表一次查询标注；
- 提示词 CRUD：GET 列表 / PUT 覆盖（upsert）/ DELETE 恢复默认；
- node_id 含空格/冒号，前端必须 encodeURIComponent（FastAPI path param 已解码）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.agents import (
    AgentPromptDTO,
    AgentPromptListData,
    AgentTopologyDTO,
    AgentTopologyNodeDTO,
    UpsertAgentPromptRequest,
)
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.graph_topology import TopologyEdgeDTO
from backend.shared.clock import SystemClock

router = APIRouter(prefix="/api/v1", tags=["agents"])


def _meta(request: Request) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id)


def _to_schema_dto(d) -> AgentPromptDTO:
    return AgentPromptDTO(
        node_id=d.node_id,
        label=d.label,
        layer=d.layer,
        default_prompt=d.default_prompt,
        override_prompt=d.override_prompt,
        has_override=d.has_override,
        updated_at=d.updated_at,
    )


@router.get("/agents/topology", response_model=Envelope[AgentTopologyDTO])
async def get_agents_topology(request: Request, trace_id: str = Depends(ensure_trace_context)):
    """静态全局拓扑（全层形态）+ 节点提示词编辑性/已自定义标注。"""

    def _do():
        from AI.graph.topology import build_topology  # 延迟导入：平台进程专属
        from AI.utils.prompts import DEFAULT_PROMPTS

        with request.app.state.analysis_services.open() as bundle:
            overrides = bundle.uow.prompts.list_as_map()
        return build_topology(("market", "sector", "screening", "stock")), DEFAULT_PROMPTS, overrides

    topology, default_prompts, overrides = await request.app.state.analysis_services.run(_do)

    nodes = [
        AgentTopologyNodeDTO(
            id=n.id,
            label=n.label,
            layer=n.layer,
            row=n.row,
            order=n.order,
            has_prompt=n.id in default_prompts,
            has_override=n.id in overrides,
        )
        for n in topology.nodes
    ]
    edges = [
        TopologyEdgeDTO(source=e.source, target=e.target, kind=e.kind, parallel=e.parallel)
        for e in topology.edges
    ]
    data = AgentTopologyDTO(nodes=nodes, edges=edges, generated_at=SystemClock().now())
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.get("/agents/prompts", response_model=Envelope[AgentPromptListData])
async def list_agent_prompts(request: Request, trace_id: str = Depends(ensure_trace_context)):
    """全部可编辑节点的默认提示词 + 覆盖叠加列表。"""

    def _do():
        with request.app.state.analysis_services.open() as bundle:
            return bundle.prompts.list_prompts()

    dtos = await request.app.state.analysis_services.run(_do)
    data = AgentPromptListData(items=[_to_schema_dto(d) for d in dtos])
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.put("/agents/prompts/{node_id}", response_model=Envelope[AgentPromptDTO])
async def upsert_agent_prompt(
    node_id: str,
    body: UpsertAgentPromptRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    """保存/更新单 Agent 提示词覆盖（对新建任务生效；进行中任务不受影响）。"""

    def _do():
        with request.app.state.analysis_services.open() as bundle:
            return bundle.prompts.upsert_prompt(node_id, body.prompt_text)

    d = await request.app.state.analysis_services.run(_do)
    return Envelope(data=_to_schema_dto(d), meta=_meta(request)).model_dump()


@router.delete("/agents/prompts/{node_id}", response_model=Envelope[AgentPromptDTO])
async def reset_agent_prompt(
    node_id: str,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    """恢复默认提示词（删除覆盖；幂等）。"""

    def _do():
        with request.app.state.analysis_services.open() as bundle:
            return bundle.prompts.reset_prompt(node_id)

    d = await request.app.state.analysis_services.run(_do)
    return Envelope(data=_to_schema_dto(d), meta=_meta(request)).model_dump()

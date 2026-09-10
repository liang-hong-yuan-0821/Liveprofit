"""Agent 提示词查询与覆盖管理（单Agent重跑与提示词编辑方案 3.3）。

- 默认提示词单一事实来源 = AI.utils.prompts.DEFAULT_PROMPTS（与内核工厂同源）；
- 覆盖存 agent_prompt_overrides 表，list 时叠加；
- 展示用默认提示词经 render_default_prompt 展开 {output_format} 锚点、移除 {date_line}；
- 校验顺序固定：拓扑内纯代码节点（Screening）422 → 不在拓扑 404 → 空/超长 422。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.modules.analysis.application.errors import (
    AgentNodeNotFoundError,
    AgentPromptNotEditableError,
)
from backend.shared.errors import DomainError

MAX_PROMPT_LENGTH = 20000


@dataclass(frozen=True)
class AgentPromptDTO:
    node_id: str
    label: str
    layer: str
    default_prompt: str
    override_prompt: str | None
    has_override: bool
    updated_at: datetime | None


def _editable_topology():
    """可编辑节点 = 拓扑主节点 ∩ DEFAULT_PROMPTS（19 个主节点；
    market 的 US/KR 4 分析师为折叠节点不在拓扑，v1 不可编辑）。"""
    from AI.graph.topology import build_topology
    from AI.utils.prompts import DEFAULT_PROMPTS

    topology = build_topology(("market", "sector", "screening", "stock"))
    by_id = {n.id: n for n in topology.nodes}
    return {nid: by_id[nid] for nid in DEFAULT_PROMPTS if nid in by_id}


class AgentPromptService:
    def __init__(self, uow, *, clock=None) -> None:
        self._uow = uow
        self._clock = clock

    def list_prompts(self) -> list[AgentPromptDTO]:
        """全部可编辑节点的默认提示词 + 覆盖叠加。"""
        from AI.utils.prompts import render_default_prompt

        overrides = self._uow.prompts.list_as_map()
        return [
            self._to_dto(node_id, node, overrides.get(node_id), override_row=None)
            for node_id, node in _editable_topology().items()
        ]

    def upsert_prompt(self, node_id: str, prompt_text: str) -> AgentPromptDTO:
        node = self._validate(node_id, prompt_text)
        now = self._now()
        row = self._uow.prompts.upsert(node_id, prompt_text, now)
        self._uow.commit()
        return self._to_dto(node_id, node, row.prompt_text, override_row=row)

    def reset_prompt(self, node_id: str) -> AgentPromptDTO:
        """恢复默认（幂等：无覆盖也返回默认态 DTO）。"""
        node = self._validate(node_id, prompt_text=None)
        row = self._uow.prompts.get(node_id)
        if row is not None:
            self._uow.prompts.delete(node_id)
            self._uow.commit()
        return self._to_dto(node_id, node, None, override_row=None)

    # ---- 内部 ----

    def _validate(self, node_id: str, prompt_text: str | None):
        topology = _editable_topology()
        node = topology.get(node_id)
        if node is None:
            if node_id == "screening:Screening":
                raise AgentPromptNotEditableError(
                    f"{node_id} 为纯代码节点，无提示词可编辑")
            raise AgentNodeNotFoundError(f"Agent 节点不存在: {node_id}")
        if prompt_text is not None:
            if not prompt_text.strip():
                raise DomainError("提示词不能为空", code="VALIDATION_ERROR")
            if len(prompt_text) > MAX_PROMPT_LENGTH:
                raise DomainError(
                    f"提示词超长（上限 {MAX_PROMPT_LENGTH} 字符）",
                    code="VALIDATION_ERROR",
                )
        return node

    def _to_dto(self, node_id: str, node, override_prompt: str | None,
                override_row=None) -> AgentPromptDTO:
        from AI.utils.prompts import render_default_prompt

        return AgentPromptDTO(
            node_id=node_id,
            label=node.label,
            layer=node.layer,
            default_prompt=render_default_prompt(node_id),
            override_prompt=override_prompt,
            has_override=bool(override_prompt),
            updated_at=override_row.updated_at if override_row is not None else None,
        )

    def _now(self) -> datetime:
        from backend.shared.clock import SystemClock

        return (self._clock or SystemClock()).now()

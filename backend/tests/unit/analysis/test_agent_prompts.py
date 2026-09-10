"""AgentPromptService 单测（单Agent重跑与提示词编辑方案 3.3）。

list 默认+覆盖叠加；upsert/reset；校验顺序（Screening 422 → 未知节点 404 → 空/超长 422）；
展示版默认提示词无锚点残留。
"""

from __future__ import annotations

import pytest

from backend.modules.analysis.application.agent_prompts import (
    MAX_PROMPT_LENGTH,
    AgentPromptService,
)
from backend.modules.analysis.application.errors import (
    AgentNodeNotFoundError,
    AgentPromptNotEditableError,
)
from backend.shared.errors import DomainError
from backend.tests.unit.analysis.fakes import FakeClock, FakeUnitOfWork


@pytest.fixture
def uow():
    return FakeUnitOfWork()


def _service(uow):
    return AgentPromptService(uow, clock=FakeClock())


def test_list_prompts_returns_editable_nodes_with_rendered_defaults(uow):
    """19 个可编辑主节点；默认提示词展示版无 {output_format}/{date_line} 锚点。"""
    dtos = _service(uow).list_prompts()
    assert len(dtos) == 19
    for d in dtos:
        assert d.has_override is False
        assert d.override_prompt is None
        assert "{output_format}" not in d.default_prompt
        assert "{date_line}" not in d.default_prompt
    # 抽查：CN News 展示版含实际输出格式模板内容
    cn_news = next(d for d in dtos if d.node_id == "market:CN News Analyst")
    assert "事件日历速览" in cn_news.default_prompt  # 模板实际内容已展开


def test_upsert_and_list_roundtrip(uow):
    service = _service(uow)
    dto = service.upsert_prompt("market:CN News Analyst", "自定义提示词全文")
    assert dto.has_override is True
    assert dto.override_prompt == "自定义提示词全文"
    # list 叠加
    listed = {d.node_id: d for d in service.list_prompts()}
    assert listed["market:CN News Analyst"].override_prompt == "自定义提示词全文"
    # 其余节点不受影响
    assert listed["sector:Sector News Analyst"].has_override is False


def test_reset_prompt_restores_default_and_is_idempotent(uow):
    service = _service(uow)
    service.upsert_prompt("market:CN News Analyst", "自定义")
    dto = service.reset_prompt("market:CN News Analyst")
    assert dto.has_override is False
    assert dto.override_prompt is None
    # 幂等：无覆盖时 reset 不抛错
    dto2 = service.reset_prompt("market:CN News Analyst")
    assert dto2.has_override is False


def test_validation_order_screening_422_before_unknown_404(uow):
    service = _service(uow)
    with pytest.raises(AgentPromptNotEditableError):
        service.upsert_prompt("screening:Screening", "x")
    with pytest.raises(AgentNodeNotFoundError):
        service.upsert_prompt("market:不存在节点", "x")


def test_validation_empty_and_too_long(uow):
    service = _service(uow)
    with pytest.raises(DomainError) as exc_info:
        service.upsert_prompt("market:CN News Analyst", "   ")
    assert exc_info.value.code == "VALIDATION_ERROR"
    with pytest.raises(DomainError) as exc_info:
        service.upsert_prompt("market:CN News Analyst", "长" * (MAX_PROMPT_LENGTH + 1))
    assert exc_info.value.code == "VALIDATION_ERROR"

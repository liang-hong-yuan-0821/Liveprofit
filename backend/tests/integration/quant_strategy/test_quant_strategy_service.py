"""QuantStrategyService 状态机集成测试（plan 4.1.1 / 4.5.4）。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from backend.modules.quant_strategy.application.errors import (
    StrategyNameConflictError,
    StrategyNotFoundError,
    StrategyRevisionConflictError,
    StrategyValidationFailedError,
    StrategyVersionInvalidStateError,
    StrategyVersionNotPublishedError,
)
from backend.modules.quant_strategy.application.service import QuantStrategyService
from backend.modules.quant_strategy.infrastructure.repositories import SqlAlchemyQuantStrategyUnitOfWork

LEGAL = '''
def strategy(context):
    close = context["ohlcv"]["close"]
    ma5 = context["indicators"]["ma_bfq_5"][-1]
    price = close[-1]
    if price > ma5:
        return {"action": "BUY", "score": 90, "entry_price": price,
                "stop_loss": round(price * 0.965, 2), "take_profit": round(price * 1.10, 2),
                "sell_ratio": None, "reason": "MA5 上穿"}
    return {"action": "HOLD", "score": 50, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "sell_ratio": None, "reason": "不满足"}
'''


def _uow(env):
    return SqlAlchemyQuantStrategyUnitOfWork(env["session_factory"])


def _create(env, name=None, source_code=""):
    if name is None:
        name = f"策略-{uuid.uuid4().hex[:8]}"
    with _uow(env) as uow:
        return QuantStrategyService(uow).create(name, source_code=source_code)


def test_create_has_v1_draft_and_get_draft_returns_source(env):
    created = _create(env, source_code=LEGAL)
    assert created.name.startswith("策略-")
    assert len(created.versions) == 1
    assert created.versions[0].version_no == 1
    assert created.versions[0].status == "DRAFT"
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        draft = service.get_draft(created.id)
        assert draft.source_code == LEGAL
        # 列表/详情 DTO 永不含源码
        dto = service.get(created.id)
        assert not hasattr(dto, "source_code")
        assert not hasattr(dto.versions[0], "source_code")


def test_publish_creates_published_plus_exactly_one_next_draft(env):
    created = _create(env, source_code=LEGAL)
    draft = created.versions[0]
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        result = service.publish(created.id, draft.id, expected_version=draft.version)
        assert result.published.status == "PUBLISHED"
        assert result.published.version_no == 1
        assert result.published.published_at is not None
        assert result.next_draft.status == "DRAFT"
        assert result.next_draft.version_no == 2
        assert result.next_draft.source_code == LEGAL
        dto = service.get(created.id)
        drafts = [v for v in dto.versions if v.status == "DRAFT"]
        published = [v for v in dto.versions if v.status == "PUBLISHED"]
        assert len(drafts) == 1
        assert len(published) == 1


def test_publish_twice_gives_two_published_versions(env):
    created = _create(env, source_code=LEGAL)
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        first = service.publish(created.id, created.versions[0].id, expected_version=1)
        second = service.publish(created.id, first.next_draft.id, expected_version=1)
        assert second.published.version_no == 2
        assert second.next_draft.version_no == 3
        dto = service.get(created.id)
        assert len([v for v in dto.versions if v.status == "PUBLISHED"]) == 2
        assert len([v for v in dto.versions if v.status == "DRAFT"]) == 1


def test_publish_rejects_invalid_source_with_issues(env):
    created = _create(env, source_code="def strategy(context):\n    import os\n")
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        with pytest.raises(StrategyValidationFailedError) as exc_info:
            service.publish(created.id, created.versions[0].id, expected_version=1)
        assert any(i.code == "FORBIDDEN_IMPORT" for i in exc_info.value.issues)


def test_save_draft_validation_failure_returns_issues_and_keeps_old_source(env):
    created = _create(env, source_code=LEGAL)
    draft = created.versions[0]
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        result = service.save_draft(
            created.id, name=created.name, description=None,
            source_code="def strategy(context):\n    import os\n",
            expected_strategy_version=created.version, expected_draft_version=draft.version,
        )
        assert result.draft is None
        assert any(i.code == "FORBIDDEN_IMPORT" for i in result.validation_issues)
        # 失败不落库：源码保持旧值
        assert service.get_draft(created.id).source_code == LEGAL


def test_save_draft_updates_meta_and_source_atomically(env):
    created = _create(env)
    draft = created.versions[0]
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        result = service.save_draft(
            created.id, name="策略A-改", description="双均线",
            source_code=LEGAL,
            expected_strategy_version=1, expected_draft_version=1,
        )
        assert result.draft is not None
        assert result.draft.version == 2
        dto = service.get(created.id)
        assert dto.name == "策略A-改"
        assert dto.description == "双均线"
        assert dto.version == 2


def test_draft_insert_failure_rolls_back_publish(env, monkeypatch):
    """新 DRAFT 插入失败必须 rollback：旧草稿仍为 DRAFT（plan 4.1.1）。

    制造确定性冲突：预插 version_no=9 的 PUBLISHED 行，并把 next_version_no
    monkeypatch 为 9——发布时「下一版 DRAFT」插入撞 (strategy_id, version_no)
    唯一键 → IntegrityError → rollback → 旧草稿仍为 DRAFT。
    """
    created = _create(env, source_code=LEGAL)
    draft_id = created.versions[0].id
    strategy_id = created.id
    with _uow(env) as uow:
        uow.session.execute(
            text(
                "INSERT INTO quant_strategy_versions (id, strategy_id, version_no, status, source_code, source_hash, version, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :sid, 9, 'PUBLISHED', 'x', :h, 1, now(), now())"
            ),
            {"sid": strategy_id, "h": "0" * 64},
        )
        uow.commit()
    monkeypatch.setattr(
        "backend.modules.quant_strategy.infrastructure.repositories.QuantStrategyVersionRepository.next_version_no",
        lambda self, strategy_id: 9,
    )
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        with pytest.raises(StrategyVersionInvalidStateError):
            service.publish(strategy_id, draft_id, expected_version=1)
        # rollback 后旧草稿仍为 DRAFT
        draft = service.get_draft(strategy_id)
        assert draft.id == draft_id
        assert draft.status == "DRAFT"


def test_illegal_state_transitions(env):
    created = _create(env, source_code=LEGAL)
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        published = service.publish(created.id, created.versions[0].id, expected_version=1).published
        # PUBLISHED 再发布 → 非法转换
        with pytest.raises(StrategyVersionInvalidStateError):
            service.publish(created.id, published.id, expected_version=published.version)
        # 归档 DRAFT → 禁止
        draft = service.get_draft(created.id)
        with pytest.raises(StrategyVersionInvalidStateError):
            service.archive(created.id, draft.id)
        # 仅剩一个 PUBLISHED 时归档 → 拒绝
        with pytest.raises(StrategyVersionInvalidStateError):
            service.archive(created.id, published.id)


def test_archive_requires_two_published_and_is_irreversible(env):
    created = _create(env, source_code=LEGAL)
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        first = service.publish(created.id, created.versions[0].id, expected_version=1)
        second = service.publish(created.id, first.next_draft.id, expected_version=1)
        archived = service.archive(created.id, first.published.id)
        assert archived.status == "ARCHIVED"
        assert archived.archived_at is not None
        # ARCHIVED 不可逆：再归档 → 非法转换
        with pytest.raises(StrategyVersionInvalidStateError):
            service.archive(created.id, first.published.id)
        # 归档后仍剩一个 PUBLISHED（v2）
        assert service.get(created.id).versions[0].version_no == 3  # 最新 draft v3
        published = [v for v in service.get(created.id).versions if v.status == "PUBLISHED"]
        assert len(published) == 1
        assert published[0].id == second.published.id


def test_get_published_rejects_draft(env):
    created = _create(env, source_code=LEGAL)
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        with pytest.raises(StrategyVersionNotPublishedError):
            service.get_published(created.versions[0].id)
        published = service.publish(created.id, created.versions[0].id, expected_version=1).published
        assert service.get_published(published.id).id == published.id


def test_name_conflict_and_not_found(env):
    _create(env, name="重名策略")
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        with pytest.raises(StrategyNameConflictError):
            service.create("重名策略")
        with pytest.raises(StrategyNotFoundError):
            service.get("00000000-0000-0000-0000-000000000000")


def test_revision_conflict_on_concurrent_save(env):
    created = _create(env)
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        service.save_draft(
            created.id, name=created.name, description=None, source_code=LEGAL,
            expected_strategy_version=1, expected_draft_version=1,
        )
    with _uow(env) as uow:
        service = QuantStrategyService(uow)
        with pytest.raises(StrategyRevisionConflictError):
            service.save_draft(
                created.id, name=created.name, description=None, source_code=LEGAL,
                expected_strategy_version=1, expected_draft_version=1,
            )

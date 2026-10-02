"""QuantStrategyService（plan 4.1.1）：创建、草稿编辑、发布、归档状态机。

- 创建策略同时创建 v1 DRAFT；partial unique index 保证每策略至多一个 DRAFT，
  create/publish 的同一事务保证正常业务路径始终至少一个可编辑 DRAFT。
- 发布：`WHERE id=:id AND status='DRAFT' AND version=:expected_version` 条件更新，
  经共享 validate_strategy_source() 校验后同一事务置 PUBLISHED、写入不可变
  published_at 并插入下一版 DRAFT；新 DRAFT 插入失败必须 rollback（旧草稿仍为 DRAFT）。
- 仅 DRAFT → PUBLISHED、PUBLISHED → ARCHIVED 有效；只允许归档 PUBLISHED，
  禁止归档 DRAFT，且至少保留一个 PUBLISHED。ARCHIVED 不可逆。
- 已发布/被历史任务引用版本不可修改或物理删除（本服务无 UPDATE/DELETE 发布版入口）。
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError

from AI.strategy_sandbox.validator import MAX_SOURCE_BYTES, StrategyValidationIssue, validate_strategy_source
from backend.modules.quant_strategy.application.contracts import (
    DraftSaveResult,
    PublishResult,
    QuantStrategyDTO,
    QuantStrategyDraftDTO,
    QuantStrategyVersionDTO,
)
from backend.modules.quant_strategy.application.errors import (
    StrategyNameConflictError,
    StrategyNotFoundError,
    StrategyRevisionConflictError,
    StrategyVersionInvalidStateError,
    StrategyVersionNotPublishedError,
    StrategyValidationFailedError,
)
from backend.modules.quant_strategy.domain.values import StrategyStatus
from backend.modules.quant_strategy.domain.templates import (
    RENDERER_VERSION,
    TemplateValidationError,
    get_template,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.lifecycle_models import LifecyclePolicyVersion
from backend.modules.quant_strategy.infrastructure.repositories import (
    QuantStrategyRepository,
    QuantStrategyVersionRepository,
)
from backend.shared.clock import Clock, SystemClock
from backend.shared.ids import new_uuid


def _source_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


class QuantStrategyService:
    def __init__(self, uow, *, clock: Clock | None = None) -> None:
        self._uow = uow
        self._strategies = QuantStrategyRepository(uow.session)
        self._versions = QuantStrategyVersionRepository(uow.session)
        self._clock = clock or SystemClock()

    # ---- 查询 ----

    def get(self, strategy_id: uuid.UUID) -> QuantStrategyDTO:
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise StrategyNotFoundError(f"策略不存在：{strategy_id}")
        return self._to_strategy_dto(strategy)

    def list_all(self) -> list[QuantStrategyDTO]:
        return [self._to_strategy_dto(s) for s in self._strategies.list_all()]

    def get_draft(self, strategy_id: uuid.UUID) -> QuantStrategyDraftDTO:
        draft = self._get_draft_or_raise(strategy_id)
        return self._to_draft_dto(draft)

    # ---- 创建 ----

    def create(
        self, name: str, description: str | None = None, source_code: str = "",
        template_id: str | None = None, template_params: dict | None = None,
    ) -> QuantStrategyDTO:
        if not source_code.strip():
            raise StrategyValidationFailedError([
                StrategyValidationIssue("SOURCE_REQUIRED", "新建策略必须输入代码", 1, 1),
            ])
        if len(source_code.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise StrategyValidationFailedError([
                StrategyValidationIssue("SOURCE_TOO_LARGE", "策略源码不能超过 12 KiB", 1, 1),
            ])
        normalized_params = None
        renderer_version = None
        if template_id is not None:
            try:
                rendered, normalized_params = get_template(template_id).render(template_params)
            except TemplateValidationError as exc:
                raise StrategyValidationFailedError([
                    StrategyValidationIssue("TEMPLATE_PARAMS_INVALID", str(exc), 1, 1),
                ]) from None
            if rendered != source_code:
                raise StrategyValidationFailedError([
                    StrategyValidationIssue(
                        "TEMPLATE_SOURCE_MISMATCH",
                        "模板元数据必须对应服务端渲染的原始源码；修改后请按自定义策略创建",
                        1, 1,
                    ),
                ])
            renderer_version = RENDERER_VERSION
        if self._strategies.get_by_name(name) is not None:
            raise StrategyNameConflictError(f"策略名已存在：{name}")
        now = self._clock.now()
        strategy = QuantStrategy(
            id=new_uuid(), name=name, description=description, version=1,
            created_at=now, updated_at=now,
        )
        self._strategies.add(strategy)
        # 代码必填；允许草稿尚未完成，发布前必须通过完整校验。
        self._versions.add(
            QuantStrategyVersion(
                id=new_uuid(), strategy_id=strategy.id, version_no=1,
                status=StrategyStatus.DRAFT.value,
                source_code=source_code, source_hash=_source_hash(source_code),
                template_id=template_id, template_params=normalized_params,
                template_renderer_version=renderer_version,
                version=1, created_at=now, updated_at=now,
            )
        )
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise StrategyNameConflictError(f"策略名已存在：{name}") from None
        return self.get(strategy.id)

    # ---- 草稿编辑（元数据 + 源码，一个原子请求） ----

    def save_draft(
        self,
        strategy_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        source_code: str,
        expected_strategy_version: int,
        expected_draft_version: int,
    ) -> DraftSaveResult:
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise StrategyNotFoundError(f"策略不存在：{strategy_id}")
        draft = self._get_draft_or_raise(strategy_id)
        # 草稿/发布共用同一校验：失败不落库，保留客户端文本
        issues = validate_strategy_source(source_code)
        if issues:
            return DraftSaveResult(draft=None, validation_issues=issues)
        if self._strategies.get_by_name(name) is not None and name != strategy.name:
            raise StrategyNameConflictError(f"策略名已存在：{name}")
        now = self._clock.now()
        if not self._strategies.conditional_update_version(
            strategy_id, expected_strategy_version,
            {"name": name, "description": description, "version": expected_strategy_version + 1, "updated_at": now},
        ):
            raise StrategyRevisionConflictError("策略已变更，请重新拉取")
        if not self._versions.conditional_update_version(
            draft.id, expected_draft_version,
            {
                "source_code": source_code,
                "source_hash": _source_hash(source_code),
                # 用户编辑后模板只保留来源审计会造成生命周期误绑定；
                # 草稿自由编辑一律转为自定义策略。
                "template_id": None,
                "template_params": None,
                "template_renderer_version": None,
                "lifecycle_policy_version_id": None,
                "version": expected_draft_version + 1,
                "updated_at": now,
            },
        ):
            self._uow.rollback()
            raise StrategyRevisionConflictError("草稿已变更，请重新拉取")
        self._uow.commit()
        updated = self._versions.get(draft.id)
        assert updated is not None
        return DraftSaveResult(draft=self._to_draft_dto(updated))

    # ---- 发布 ----

    def publish(
        self, strategy_id: uuid.UUID, version_id: uuid.UUID, expected_version: int
    ) -> PublishResult:
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise StrategyNotFoundError(f"策略不存在：{strategy_id}")
        version = self._versions.get(version_id)
        if version is None or version.strategy_id != strategy_id:
            raise StrategyNotFoundError(f"策略版本不存在：{version_id}")
        if version.status != StrategyStatus.DRAFT.value:
            raise StrategyVersionInvalidStateError("只有 DRAFT 可以发布")
        # 发布前再校验一次源码（与草稿保存共用 validate_strategy_source）
        issues = validate_strategy_source(version.source_code)
        if issues:
            raise StrategyValidationFailedError(issues)
        now = self._clock.now()
        if not self._versions.conditional_update_version(
            version_id, expected_version,
            {
                "status": StrategyStatus.PUBLISHED.value,
                "published_at": now,
                "version": expected_version + 1,
                "updated_at": now,
            },
        ):
            raise StrategyRevisionConflictError("草稿已变更，请重新拉取")
        # 同一事务插入下一版 DRAFT；插入失败必须 rollback，使旧草稿仍为 DRAFT
        next_draft = QuantStrategyVersion(
            id=new_uuid(), strategy_id=strategy_id,
            version_no=self._versions.next_version_no(strategy_id),
            status=StrategyStatus.DRAFT.value,
            source_code=version.source_code, source_hash=version.source_hash,
            template_id=version.template_id, template_params=version.template_params,
            template_renderer_version=version.template_renderer_version,
            lifecycle_policy_version_id=version.lifecycle_policy_version_id,
            version=1, created_at=now, updated_at=now,
        )
        self._versions.add(next_draft)
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise StrategyVersionInvalidStateError("发布失败：无法创建下一版草稿") from None
        published = self._versions.get(version_id)
        assert published is not None
        return PublishResult(
            published=self._to_version_dto(published), next_draft=self._to_draft_dto(next_draft)
        )

    # ---- 归档 ----

    def archive(self, strategy_id: uuid.UUID, version_id: uuid.UUID) -> QuantStrategyVersionDTO:
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise StrategyNotFoundError(f"策略不存在：{strategy_id}")
        version = self._versions.get(version_id)
        if version is None or version.strategy_id != strategy_id:
            raise StrategyNotFoundError(f"策略版本不存在：{version_id}")
        if version.status != StrategyStatus.PUBLISHED.value:
            raise StrategyVersionInvalidStateError("只有 PUBLISHED 可以归档，禁止归档 DRAFT")
        if self._versions.count_published(strategy_id) <= 1:
            raise StrategyVersionInvalidStateError("至少保留一个 PUBLISHED 版本")
        now = self._clock.now()
        if not self._versions.conditional_update_version(
            version_id, version.version,
            {
                "status": StrategyStatus.ARCHIVED.value,
                "archived_at": now,
                "version": version.version + 1,
                "updated_at": now,
            },
        ):
            raise StrategyRevisionConflictError("版本已变更，请重新拉取")
        self._uow.commit()
        archived = self._versions.get(version_id)
        assert archived is not None
        return self._to_version_dto(archived)

    def get_published(self, version_id: uuid.UUID) -> QuantStrategyVersionDTO:
        """任务提交校验用：读取 PUBLISHED 版本，否则 409。"""
        version = self._versions.get(version_id)
        if version is None:
            raise StrategyNotFoundError(f"策略版本不存在：{version_id}")
        if version.status != StrategyStatus.PUBLISHED.value:
            raise StrategyVersionNotPublishedError(f"策略版本未发布：{version_id}")
        return self._to_version_dto(version)

    def bind_lifecycle_policy(
        self, strategy_id: uuid.UUID, version_id: uuid.UUID,
        lifecycle_policy_version_id: uuid.UUID | None, expected_version: int,
    ) -> QuantStrategyDraftDTO:
        version = self._versions.get(version_id)
        if version is None or version.strategy_id != strategy_id:
            raise StrategyNotFoundError(f"策略版本不存在：{version_id}")
        if version.status != StrategyStatus.DRAFT.value:
            raise StrategyVersionInvalidStateError("只有 DRAFT 可以绑定生命周期策略")
        if lifecycle_policy_version_id is not None:
            policy = self._uow.session.get(LifecyclePolicyVersion, lifecycle_policy_version_id)
            if policy is None:
                raise StrategyNotFoundError(f"生命周期策略版本不存在：{lifecycle_policy_version_id}")
            if policy.status != "PUBLISHED":
                raise StrategyVersionInvalidStateError("只能绑定已发布的生命周期策略版本")
            if "management_policy" in (policy.config or {}):
                from .lifecycle_service import LifecyclePolicyService
                from .errors import FillValidationError
                try:
                    LifecyclePolicyService.read_family(policy, version.template_id)
                except FillValidationError as exc:
                    raise StrategyVersionInvalidStateError(str(exc)) from exc
            else:
                policy_template_id = (policy.config or {}).get("template_id")
                if not isinstance(policy_template_id, str):
                    raise StrategyVersionInvalidStateError("生命周期策略必须显式声明 template_id")
                try:
                    get_template(policy_template_id)
                except TemplateValidationError:
                    raise StrategyVersionInvalidStateError("生命周期策略 template_id 不在七模板合同内") from None
                if version.template_id is not None and version.template_id != policy_template_id:
                    raise StrategyVersionInvalidStateError("生命周期策略与参考模板不兼容")
                try:
                    reward = Decimal(str((policy.config or {}).get("reward_multiple")))
                except (InvalidOperation, TypeError):
                    raise StrategyVersionInvalidStateError("生命周期策略缺少有效 reward_multiple") from None
                if not reward.is_finite() or reward <= 0:
                    raise StrategyVersionInvalidStateError("生命周期策略 reward_multiple 必须大于 0")
                if str((policy.config or {}).get("initial_exposure_pct", "0.50")) != "0.50":
                    raise StrategyVersionInvalidStateError("生命周期首仓比例固定为 0.50")
        if not self._versions.conditional_update_version(
            version.id, expected_version,
            {
                "lifecycle_policy_version_id": lifecycle_policy_version_id,
                "version": expected_version + 1,
                "updated_at": self._clock.now(),
            },
        ):
            raise StrategyRevisionConflictError("草稿已变更，请重新拉取")
        self._uow.commit()
        updated = self._versions.get(version.id)
        assert updated is not None
        return self._to_draft_dto(updated)

    # ---- 内部 ----

    def _get_draft_or_raise(self, strategy_id: uuid.UUID) -> QuantStrategyVersion:
        draft = self._versions.get_draft(strategy_id)
        if draft is None:
            raise StrategyNotFoundError(f"策略不存在或没有草稿：{strategy_id}")
        return draft

    def _to_strategy_dto(self, s: QuantStrategy) -> QuantStrategyDTO:
        versions = self._versions.list_by_strategy(s.id)
        return QuantStrategyDTO(
            id=s.id, name=s.name, description=s.description, version=s.version,
            created_at=s.created_at, updated_at=s.updated_at,
            versions=[self._to_version_dto(v) for v in versions],
        )

    def _to_version_dto(self, v: QuantStrategyVersion) -> QuantStrategyVersionDTO:
        return QuantStrategyVersionDTO(
            id=v.id, strategy_id=v.strategy_id, version_no=v.version_no, status=v.status,
            source_hash=v.source_hash, published_at=v.published_at, archived_at=v.archived_at,
            template_id=v.template_id, template_params=v.template_params,
            template_renderer_version=v.template_renderer_version,
            lifecycle_policy_version_id=v.lifecycle_policy_version_id,
            version=v.version, created_at=v.created_at, updated_at=v.updated_at,
        )

    def _to_draft_dto(self, v: QuantStrategyVersion) -> QuantStrategyDraftDTO:
        return QuantStrategyDraftDTO(
            id=v.id, strategy_id=v.strategy_id, version_no=v.version_no, status=v.status,
            source_code=v.source_code, source_hash=v.source_hash, version=v.version,
            template_id=v.template_id, template_params=v.template_params,
            template_renderer_version=v.template_renderer_version,
            lifecycle_policy_version_id=v.lifecycle_policy_version_id,
            created_at=v.created_at, updated_at=v.updated_at,
        )

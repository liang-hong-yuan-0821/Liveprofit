"""量化策略路由（plan 4.1.1/4.4.1）。

- 草稿读取/保存是唯一可返回 source_code 的接口；
- STRATEGY_VALIDATION_FAILED(422) 的 detail 携带校验问题列表（code/message/line/column），
  不含客户端提交的源码文本；
- 非法状态转换 STRATEGY_VERSION_INVALID_STATE(409)、并发冲突 STRATEGY_REVISION_CONFLICT(409)。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.quant_strategies import (
    QuantStrategyCreateRequest,
    QuantStrategyDTO,
    QuantStrategyDraftDTO,
    QuantStrategyDraftUpdateRequest,
    QuantStrategyFormatData,
    QuantStrategyFormatRequest,
    QuantStrategyListData,
    QuantStrategyPublishData,
    QuantStrategyPublishRequest,
    QuantStrategyVersionDTO,
    LifecyclePolicyBindingRequest,
    QuantStrategyTemplateDTO,
    QuantStrategyTemplateListData,
)
from backend.api.dependencies import open_quant_strategy_uow as _open_uow
from backend.modules.quant_strategy.application.service import QuantStrategyService
from backend.modules.quant_strategy.domain.templates import RENDERER_VERSION, list_templates

router = APIRouter(prefix="/api/v1", tags=["quant-strategies"])


@router.get("/quant-strategy-templates", response_model=Envelope[QuantStrategyTemplateListData])
async def list_strategy_templates(request: Request, trace_id: str = Depends(ensure_trace_context)):
    items = []
    for definition in list_templates():
        source, params = definition.render()
        items.append(QuantStrategyTemplateDTO(
            template_id=definition.template_id,
            display_name=definition.display_name,
            description=definition.description,
            required_bars=definition.required_bars,
            wave=definition.wave,
            renderer_version=RENDERER_VERSION,
            default_params=params,
            source_code=source,
        ))
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=QuantStrategyTemplateListData(items=items), meta=meta).model_dump()


@router.get("/quant-strategies", response_model=Envelope[QuantStrategyListData])
async def list_strategies(request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).list_all()

    dtos = await services.run(_do)
    items = [
        QuantStrategyDTO(
            id=s.id, name=s.name, description=s.description, version=s.version,
            created_at=s.created_at, updated_at=s.updated_at,
            versions=[_version(v) for v in s.versions],
        )
        for s in dtos
    ]
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=QuantStrategyListData(items=items), meta=meta).model_dump()


@router.post("/quant-strategies/format", response_model=Envelope[QuantStrategyFormatData])
async def format_strategy_source(
    payload: QuantStrategyFormatRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    """本地 ruff 格式化策略源码（编辑器体验；不落库、不校验七键合同）。"""
    import subprocess
    import sys

    from backend.api.schemas.problem import ProblemError

    services = request.app.state.analysis_services

    def _do():
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "ruff", "format", "-"],
                input=payload.source_code.encode("utf-8"),
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ProblemError(422, "STRATEGY_FORMAT_FAILED", "格式化超时") from None
        if proc.returncode != 0:
            raise ProblemError(422, "STRATEGY_FORMAT_FAILED", "代码格式化失败，请检查 Python 语法")
        return proc.stdout.decode("utf-8")

    formatted = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=QuantStrategyFormatData(source_code=formatted), meta=meta).model_dump()


@router.post("/quant-strategies", status_code=201, response_model=Envelope[QuantStrategyDTO])
async def create_strategy(
    payload: QuantStrategyCreateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).create(
                payload.name, description=payload.description, source_code=payload.source_code,
                template_id=payload.template_id, template_params=payload.template_params,
            )

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_strategy(dto), meta=meta).model_dump()


@router.get("/quant-strategies/{strategy_id}", response_model=Envelope[QuantStrategyDTO])
async def get_strategy(
    strategy_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).get(strategy_id)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_strategy(dto), meta=meta).model_dump()


@router.get("/quant-strategies/{strategy_id}/draft", response_model=Envelope[QuantStrategyDraftDTO])
async def get_draft(
    strategy_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).get_draft(strategy_id)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_draft(dto), meta=meta).model_dump()


@router.put("/quant-strategies/{strategy_id}/draft", response_model=Envelope[QuantStrategyDraftDTO])
async def save_draft(
    strategy_id: uuid.UUID,
    payload: QuantStrategyDraftUpdateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).save_draft(
                strategy_id,
                name=payload.name,
                description=payload.description,
                source_code=payload.source_code,
                expected_strategy_version=payload.expected_strategy_version,
                expected_draft_version=payload.expected_draft_version,
            )

    result = await services.run(_do)
    if result.draft is None:
        # 校验失败：422（detail 为紧凑问题列表文本，不含客户端提交的源码）
        from backend.api.schemas.problem import ProblemError

        issues_text = "; ".join(
            f"{i.code}(行{i.line}) {i.message}" for i in result.validation_issues
        )
        raise ProblemError(
            422,
            "STRATEGY_VALIDATION_FAILED",
            f"策略源码校验失败：{issues_text}",
            retryable=False,
        )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_draft(result.draft), meta=meta).model_dump()


@router.post(
    "/quant-strategies/{strategy_id}/versions/{version_id}/publish",
    response_model=Envelope[QuantStrategyPublishData],
)
async def publish_version(
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: QuantStrategyPublishRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).publish(strategy_id, version_id, payload.expected_version)

    result = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(
        data=QuantStrategyPublishData(published=_version(result.published), next_draft=_draft(result.next_draft)),
        meta=meta,
    ).model_dump()


@router.put(
    "/quant-strategies/{strategy_id}/versions/{version_id}/lifecycle-policy",
    response_model=Envelope[QuantStrategyDraftDTO],
)
async def bind_lifecycle_policy(
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: LifecyclePolicyBindingRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).bind_lifecycle_policy(
                strategy_id, version_id, payload.lifecycle_policy_version_id, payload.expected_version,
            )

    dto = await services.run(_do)
    return Envelope(data=_draft(dto), meta=EnvelopeMeta(request_id=request.state.trace_id)).model_dump()


@router.post(
    "/quant-strategies/{strategy_id}/versions/{version_id}/archive",
    response_model=Envelope[QuantStrategyVersionDTO],
)
async def archive_version(
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return QuantStrategyService(uow).archive(strategy_id, version_id)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_version(dto), meta=meta).model_dump()


def _strategy(dto) -> QuantStrategyDTO:
    return QuantStrategyDTO(
        id=dto.id, name=dto.name, description=dto.description, version=dto.version,
        created_at=dto.created_at, updated_at=dto.updated_at,
        versions=[_version(v) for v in dto.versions],
    )


def _version(dto) -> QuantStrategyVersionDTO:
    return QuantStrategyVersionDTO(
        id=dto.id, strategy_id=dto.strategy_id, version_no=dto.version_no, status=dto.status,
        source_hash=dto.source_hash, published_at=dto.published_at, archived_at=dto.archived_at,
        template_id=dto.template_id, template_params=dto.template_params,
        template_renderer_version=dto.template_renderer_version,
        lifecycle_policy_version_id=dto.lifecycle_policy_version_id,
        version=dto.version, created_at=dto.created_at, updated_at=dto.updated_at,
    )


def _draft(dto) -> QuantStrategyDraftDTO:
    return QuantStrategyDraftDTO(
        id=dto.id, strategy_id=dto.strategy_id, version_no=dto.version_no, status=dto.status,
        source_code=dto.source_code, source_hash=dto.source_hash, version=dto.version,
        template_id=dto.template_id, template_params=dto.template_params,
        template_renderer_version=dto.template_renderer_version,
        lifecycle_policy_version_id=dto.lifecycle_policy_version_id,
        created_at=dto.created_at, updated_at=dto.updated_at,
    )

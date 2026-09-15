"""EventStudyReviewService（同步方法，Router 经 API 分析服务线程池执行）。

行为对齐 Streamlit 审核界面（review_app.py），关键语义：
- 批量逐行处理：行级失败（草稿缺失 / DB 数据错误）不失败整批，任何失败路径统一
  rollback 后继续下一行（修复 Streamlit 版事务毒化隐患）。
- approve 成功即同 conn 计算影响窗口，compute 失败仅记 compute_status="failed"
  （daily_job 步骤 6 兜底重算）。
- review_fields 仅含非 None 键：键缺失委托 review_dao 既有默认链
  （importance → importance_hint 或 3；数值键 → 落 NULL；路由字段 → market + []）。
- approve 行提交前先做作用域/引用前置校验（与 review_dao 内校验同一规则，双保险）：
  校验失败 = 行级 REVIEW_ROW_FAILED（草稿保留、表单可修正后重提），
  不得映射 REVIEW_DRAFT_NOT_FOUND。
"""

from __future__ import annotations

import logging
import uuid

from backend.modules.event_study.application.review_contracts import (
    ConfirmImpactsResult,
    ComputeResult,
    ImpactDraftDTO,
    PendingEventDTO,
    PrelabelResult,
    RefreshResult,
    ReviewBatchResult,
    ReviewRowCommand,
    ReviewRowResult,
)
from backend.modules.event_study.application.review_errors import (
    ReviewComputeFailedError,
    ReviewDraftNotFoundError,
    ReviewEventNotFoundError,
    ReviewUpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

_ROW_ERROR_DRAFT_NOT_FOUND = "REVIEW_DRAFT_NOT_FOUND"
_ROW_ERROR_FAILED = "REVIEW_ROW_FAILED"  # 行级信息码：不抛 HTTP、不进 _CODE_MAP

# 行级字段长度上限（评审 m18）：与 AI/eventStudy/db/schema.sql 的 events 列宽一致。
# 长度约束不下沉到 API Schema（Schema 级 max_length 失败 = 整批 422，单行超长会
# 拖垮同批其余合法行）；此处逐行判定 → 该行 REVIEW_ROW_FAILED，其余行照常提交。
_ROW_TEXT_LIMITS = (
    ("event_type", 64, "事件类型"),
    ("event_subtype", 64, "事件子类型"),
    ("event_condition", 32, "事件条件"),
    ("event_scope", 16, "作用域"),
    ("operator", 64, "操作人"),
)
_MAX_SCOPE_REFS = 20        # 与 review_dao.MAX_SCOPE_REFS 一致
_MAX_SCOPE_REF_LEN = 64     # 单条引用上限（规范形态最长 CONCEPT:BK9999.DC 等）

_REFRESH_LOCK_KEY = "events:refresh_lock"
_REFRESH_LOCK_TTL_MS = 120_000  # 覆盖启用源串行抓取最坏 ~75s（财联社 v1 失败回退 nodeapi）+ 去重扫描


class EventStudyReviewService:
    def __init__(self, *, adapter) -> None:
        self._adapter = adapter

    # ==================== 待审核事件 ====================

    def _require_redis(self) -> None:
        if not self._adapter.redis_available():
            raise ReviewUpstreamUnavailableError("审核草稿区（Redis）不可用，请稍后重试")

    def list_pending_events(self) -> list[PendingEventDTO]:
        self._require_redis()
        return [self._to_pending_dto(d) for d in self._adapter.list_pending_events()]

    @staticmethod
    def _to_pending_dto(draft: dict) -> PendingEventDTO:
        return PendingEventDTO(
            draft_id=int(draft.get("draft_id", 0)),
            title=draft.get("title") or "",
            announced_at=draft.get("announced_at"),
            source=draft.get("source"),
            content=draft.get("content"),
            source_url=draft.get("source_url"),
            importance_hint=draft.get("importance_hint"),
            ai_suggestions=draft.get("ai_suggestions"),
        )

    def prelabel(self, limit: int) -> PrelabelResult:
        """对尚无 ai_suggestions 的草稿切片做 AI 预填（幂等；LLM 不可用返回 0 不报错）。"""
        self._require_redis()
        unlabeled = [d for d in self._adapter.list_pending_events() if not d.get("ai_suggestions")]
        prelabeled = self._adapter.prelabel(unlabeled[:limit])
        # remaining = 执行后重新列草稿、仍无建议的数量（前端循环收敛依据）
        remaining = sum(1 for d in self._adapter.list_pending_events() if not d.get("ai_suggestions"))
        return PrelabelResult(prelabeled=prelabeled, remaining=remaining)

    def refresh_events(self) -> RefreshResult:
        """采集各源最新事件写入待审草稿（审核页自动拉取）。

        失败语义：Redis 不可用 → 抛 503（与其余端点一致）；锁占用 →
        skipped_reason="locked"（另一拉取进行中，幂等跳过）；抓取/保存异常 →
        记 warning skipped_reason="failed"（列表仍可用）；正常完成 → None。
        """
        self._require_redis()
        token = uuid.uuid4().hex  # 每次调用独立 token：释放时 Lua 比对，防误删后继持锁方
        if not self._adapter.try_acquire_refresh_lock(_REFRESH_LOCK_KEY, token, _REFRESH_LOCK_TTL_MS):
            logger.info("最新事件拉取进行中（锁占用），本次跳过")
            return RefreshResult(fetched=0, new_drafts=0, skipped_reason="locked")
        try:
            events = self._adapter.fetch_latest_events()
            conn = self._adapter.open_connection()
            try:
                new_ids = self._adapter.save_pending_events(events, conn=conn)
            finally:
                conn.close()
            return RefreshResult(fetched=len(events), new_drafts=len(new_ids))
        except Exception as e:
            logger.warning("最新事件拉取失败: %s", e)
            return RefreshResult(fetched=0, new_drafts=0, skipped_reason="failed")
        finally:
            self._adapter.release_refresh_lock(_REFRESH_LOCK_KEY, token)

    # ==================== 批量提交 ====================

    @staticmethod
    def _review_fields(row: ReviewRowCommand) -> dict:
        """仅含非 None 键——键缺失委托 review_dao 默认链
        （importance_hint 或 3 / 落 NULL / 路由字段 market + []）。

        空列表是合法值（market 作用域的 affected_scope_refs=[]），同样保留。
        """
        return {
            k: v
            for k, v in {
                "event_type": row.event_type,
                "event_subtype": row.event_subtype,
                "event_condition": row.event_condition,
                "importance": row.importance,
                "expected_value": row.expected_value,
                "actual_value": row.actual_value,
                "previous_value": row.previous_value,
                "event_scope": row.event_scope,
                "affected_scope_refs": row.affected_scope_refs,
            }.items()
            if v is not None
        }

    def submit_batch(self, rows: list[ReviewRowCommand]) -> ReviewBatchResult:
        self._require_redis()
        results: list[ReviewRowResult] = []
        conn = self._adapter.open_connection()
        try:
            for row in rows:
                results.append(self._submit_row(conn, row))
        finally:
            conn.close()
        # 汇总计数由行结果推导（ReviewBatchResult 为冻结 dataclass，不在循环内改计数）
        return ReviewBatchResult(
            results=results,
            approved=sum(1 for r in results if r.ok and r.compute_status != "skipped"),
            ignored=sum(1 for r in results if r.ok and r.compute_status == "skipped"),
            computed=sum(1 for r in results if r.compute_status == "ok"),
        )

    @staticmethod
    def _validate_row_lengths(row: ReviewRowCommand) -> str | None:
        """行级字段长度校验（评审 m18）：超限 → 返回错误信息（该行行级失败）。

        与 API Schema 的分工：Schema 只留宽松上限防超大 payload，业务长度在
        行级判定——这样单个超长字段不会让整批 422（行级失败语义，草稿保留可
        修正后重提）。上限与 `events` 表列宽一致。
        """
        for field, limit, label in _ROW_TEXT_LIMITS:
            value = getattr(row, field, None)
            if isinstance(value, str) and len(value) > limit:
                return f"{label}长度超限（{len(value)} > {limit}）"
        refs = getattr(row, "affected_scope_refs", None)
        if refs:
            # 数量按**去重后**条数判定（评审残留）：合法重复引用（同一目标写两次）
            # 经 `normalize_scope_refs` 去重，原始条数超限不得误伤该行
            unique_count = len({str(r).strip() for r in refs})
            if unique_count > _MAX_SCOPE_REFS:
                return f"目标引用数量超出上限 {_MAX_SCOPE_REFS}: {unique_count}"
            for ref in refs:
                if isinstance(ref, str) and len(ref) > _MAX_SCOPE_REF_LEN:
                    return (f"目标引用长度超限（{len(ref)} > {_MAX_SCOPE_REF_LEN}）: "
                            f"{ref[:32]}…")
        return None

    def _submit_row(self, conn, row: ReviewRowCommand) -> ReviewRowResult:
        if row.action not in ("approve", "ignore"):
            return ReviewRowResult(
                row.draft_id, ok=False,
                error_code=_ROW_ERROR_FAILED, error_message=f"未知操作: {row.action}",
            )
        fields = self._review_fields(row)
        if row.action == "approve":
            # 提交前前置校验（长度 → 作用域/引用格式/引用存在性/作用域—目标组合）：
            # 失败即行级失败 REVIEW_ROW_FAILED（草稿保留可修正重提），
            # 不得映射 REVIEW_DRAFT_NOT_FOUND；review_dao.approve_event 内同一
            # 规则再校验一次（双保险）。
            length_error = self._validate_row_lengths(row)
            if length_error:
                return ReviewRowResult(
                    row.draft_id, ok=False,
                    error_code=_ROW_ERROR_FAILED, error_message=length_error,
                )
            scope_error = self._adapter.validate_scope(conn, fields)
            if scope_error:
                conn.rollback()  # 校验可能留下 aborted 事务，恢复干净状态再处理后续行
                return ReviewRowResult(
                    row.draft_id, ok=False,
                    error_code=_ROW_ERROR_FAILED, error_message=scope_error,
                )
        try:
            if row.action == "approve":
                event_id = self._adapter.approve(conn, row.draft_id, fields, row.operator)
                compute_status = self._compute_after_approve(conn, event_id)
                return ReviewRowResult(row.draft_id, ok=True, event_id=event_id, compute_status=compute_status)
            event_id = self._adapter.ignore(conn, row.draft_id, row.operator)
            return ReviewRowResult(row.draft_id, ok=True, event_id=event_id, compute_status="skipped")
        except ValueError as e:
            conn.rollback()
            return ReviewRowResult(
                row.draft_id, ok=False,
                error_code=_ROW_ERROR_DRAFT_NOT_FOUND, error_message=str(e),
            )
        except Exception as e:
            conn.rollback()
            logger.warning("审核行处理失败: draft_id=%s action=%s: %s", row.draft_id, row.action, e)
            return ReviewRowResult(
                row.draft_id, ok=False,
                error_code=_ROW_ERROR_FAILED, error_message=str(e),
            )

    @staticmethod
    def _compute_verdict(draft: dict) -> str:
        """折叠计算草稿为 ok/failed：零窗口（资产未初始化等）或全部窗口带 error → failed。

        与补算端点共用同一判定，避免「批量路径 ok、补算路径 failed」的状态矛盾。
        """
        windows = [r for a in draft.get("assets", {}).values() for r in a.values()]
        if not windows:
            return "failed"  # 什么都没算：不误报成功
        if all(r.get("error") for r in windows):
            return "failed"
        return "ok"

    def _compute_after_approve(self, conn, event_id: int) -> str:
        """通过后立即计算影响窗口（同 conn，Streamlit 先例）；失败仅记 failed，不失败该行。"""
        try:
            draft = self._adapter.compute_windows(conn, event_id)
            verdict = self._compute_verdict(draft)
            if verdict == "failed":
                logger.warning("影响计算无有效窗口（daily_job 兜底）: event_id=%s", event_id)
            return verdict
        except Exception as e:
            conn.rollback()  # compute 可能留下 aborted 事务毒化后续行
            logger.warning("影响计算失败（daily_job 兜底重算）: event_id=%s: %s", event_id, e)
            return "failed"

    def compute_for_event(self, event_id: int, operator: str) -> ComputeResult:
        """补算端点：覆盖影响草稿并刷新 TTL；事件不存在 → 404；其余异常 → 500。

        计算不抛异常但全部窗口带 error 时（如行情不足）折叠为 200 status="failed"
        而非 500——与 Streamlit「计算失败仅 warning、daily_job 兜底」行为一致。
        """
        self._require_redis()
        conn = self._adapter.open_connection()
        try:
            draft = self._adapter.compute_windows(conn, event_id)
        except ValueError as e:
            raise ReviewEventNotFoundError(str(e)) from e
        except Exception as e:
            logger.warning("补算失败: event_id=%s: %s", event_id, e)
            raise ReviewComputeFailedError(f"影响计算失败: {e}") from e
        finally:
            conn.close()
        verdict = self._compute_verdict(draft)
        if verdict == "failed":
            windows = [r for a in draft.get("assets", {}).values() for r in a.values()]
            message = (
                "未计算出任何窗口（可能市场资产未初始化），次日批处理会兜底重算"
                if not windows
                else "全部窗口计算失败（次日批处理会兜底重算）"
            )
            return ComputeResult(event_id=event_id, status="failed", message=message)
        return ComputeResult(event_id=event_id, status="ok")

    # ==================== 影响结果确认 ====================

    def list_impact_drafts(self) -> list[ImpactDraftDTO]:
        self._require_redis()
        drafts = self._adapter.list_impact_drafts()
        conn = self._adapter.open_connection()
        try:
            return [
                ImpactDraftDTO(
                    event_id=int(d.get("event_id", 0)),
                    title=self._adapter.event_title(conn, int(d.get("event_id", 0))),
                    t0=d.get("t0"),
                    computed_at=d.get("computed_at"),
                    assets=d.get("assets") or {},
                )
                for d in drafts
            ]
        finally:
            conn.close()

    def confirm_impacts(self, event_id: int, tickers: list, operator: str) -> ConfirmImpactsResult:
        self._require_redis()
        conn = self._adapter.open_connection()
        try:
            inserted = self._adapter.confirm_impacts(conn, event_id, tickers, operator)
        except ValueError as e:
            raise ReviewDraftNotFoundError(str(e)) from e
        finally:
            conn.close()
        return ConfirmImpactsResult(event_id=event_id, inserted=inserted)

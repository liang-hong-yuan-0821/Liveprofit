"""EventStudyReviewService 单测（FakeAdapter 注入，无需真实 PG/Redis/LLM）。"""

from __future__ import annotations

import pytest

from backend.modules.event_study.application.review_contracts import RefreshResult, ReviewRowCommand
from backend.modules.event_study.application.review_errors import (
    ReviewComputeFailedError,
    ReviewDraftNotFoundError,
    ReviewEventNotFoundError,
    ReviewUpstreamUnavailableError,
)
from backend.modules.event_study.application.review_service import EventStudyReviewService


class FakeConn:
    def __init__(self):
        self.rollbacks = 0
        self.closed = False

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeAdapter:
    def __init__(self):
        self.redis_ok = True
        self.conn = FakeConn()
        self.open_count = 0
        self.pending_script: list[list[dict]] = []
        self.pending_calls = 0
        self.prelabel_batches: list[list[dict]] = []
        self.prelabel_return = 0
        self.approve_errors: dict[int, Exception] = {}
        self.approve_calls: list[tuple] = []
        self.ignore_calls: list[tuple] = []
        self.compute_error: Exception | None = None
        self.compute_return: dict | None = None
        self.compute_calls: list[tuple] = []
        self.impact_drafts: list[dict] = []
        self.titles: dict[int, str] = {}
        self.confirm_return = 0
        self.confirm_error: Exception | None = None
        self.confirm_calls: list[tuple] = []
        self.fetch_events: list[dict] = []
        self.fetch_error: Exception | None = None
        self.fetch_calls = 0
        self.save_return: list[int] = []
        self.save_error: Exception | None = None
        self.save_calls: list[tuple] = []
        self.lock_acquire_ok = True
        self.lock_acquires: list[tuple] = []
        self.lock_releases: list[tuple] = []
        # 作用域前置校验：默认通过（None），置为错误文案即模拟行级校验失败；
        # scope_errors 为逐行脚本（按调用序出队），None 表示该行校验通过
        self.scope_error: str | None = None
        self.scope_errors: list[str | None] = []
        self.scope_calls: list[tuple] = []

    # ---- 适配器接口 ----
    def open_connection(self):
        self.open_count += 1
        return self.conn

    def redis_available(self) -> bool:
        return self.redis_ok

    def list_pending_events(self) -> list[dict]:
        if not self.pending_script:
            return []
        idx = min(self.pending_calls, len(self.pending_script) - 1)
        self.pending_calls += 1
        return self.pending_script[idx]

    def validate_scope(self, conn, fields) -> str | None:
        self.scope_calls.append((conn, dict(fields)))
        if self.scope_errors:
            return self.scope_errors.pop(0)
        return self.scope_error

    def approve(self, conn, draft_id, fields, operator):
        self.approve_calls.append((conn, draft_id, fields, operator))
        if draft_id in self.approve_errors:
            raise self.approve_errors[draft_id]
        return 100 + draft_id

    def ignore(self, conn, draft_id, operator):
        self.ignore_calls.append((conn, draft_id, operator))
        return 200 + draft_id

    def compute_windows(self, conn, event_id):
        self.compute_calls.append((conn, event_id))
        if self.compute_error is not None:
            raise self.compute_error
        if self.compute_return is not None:
            return self.compute_return
        # 默认含一个正常窗口，使折叠判定为 ok；空 assets / 全 error 语义由专用用例覆盖
        return {
            "event_id": event_id,
            "assets": {"000001.SH": {"event_day": {"window_days": 1, "cumulative_abnormal_return": 0.0}}},
        }

    def prelabel(self, drafts):
        self.prelabel_batches.append(drafts)
        return self.prelabel_return

    def fetch_latest_events(self):
        self.fetch_calls += 1
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.fetch_events

    def save_pending_events(self, events, conn):
        self.save_calls.append((events, conn))
        if self.save_error is not None:
            raise self.save_error
        return self.save_return

    def try_acquire_refresh_lock(self, key, token, ttl_ms):
        self.lock_acquires.append((key, token, ttl_ms))
        return self.lock_acquire_ok

    def release_refresh_lock(self, key, token):
        self.lock_releases.append((key, token))

    def list_impact_drafts(self):
        return self.impact_drafts

    def event_title(self, conn, event_id):
        return self.titles.get(event_id, f"事件 {event_id}")

    def confirm_impacts(self, conn, event_id, tickers, operator):
        self.confirm_calls.append((conn, event_id, tickers, operator))
        if self.confirm_error is not None:
            raise self.confirm_error
        return self.confirm_return


@pytest.fixture()
def svc():
    adapter = FakeAdapter()
    return adapter, EventStudyReviewService(adapter=adapter)


def _draft(**overrides) -> dict:
    d = {
        "draft_id": 1,
        "title": "CPI 数据",
        "announced_at": "2026-08-01T09:00:00+08:00",
        "source": "金十数据",
        "content": "正文",
        "source_url": "https://example.com/1",
        "importance_hint": 4,
        "ai_suggestions": {"event_type": "宏观数据", "importance": 5},
    }
    d.update(overrides)
    return d


# ==================== 列草稿 ====================

def test_list_pending_events_maps_dto_with_defensive_defaults(svc):
    adapter, service = svc
    adapter.pending_script = [[_draft(), {"draft_id": 2}]]
    items = service.list_pending_events()
    assert len(items) == 2
    first = items[0]
    assert first.draft_id == 1 and first.title == "CPI 数据"
    assert first.importance_hint == 4
    assert first.ai_suggestions == {"event_type": "宏观数据", "importance": 5}
    second = items[1]
    assert second.draft_id == 2 and second.title == "" and second.importance_hint is None


def test_list_pending_events_redis_unavailable_raises_503(svc):
    adapter, service = svc
    adapter.redis_ok = False
    with pytest.raises(ReviewUpstreamUnavailableError):
        service.list_pending_events()


# ==================== AI 预填 ====================

def test_prelabel_slices_only_unlabeled_and_recounts_remaining(svc):
    adapter, service = svc
    labeled = _draft(draft_id=1)
    unlabeled_1 = _draft(draft_id=2, ai_suggestions=None)
    unlabeled_2 = _draft(draft_id=3, ai_suggestions=None)
    adapter.pending_script = [
        [labeled, unlabeled_1, unlabeled_2],   # 切片前
        [labeled, unlabeled_2],                # 执行后仍无建议（unlabeled_1 已预填）
    ]
    adapter.prelabel_return = 1
    result = service.prelabel(limit=1)
    assert adapter.prelabel_batches == [[unlabeled_1]]  # 仅无建议切片 + limit 截断
    assert result.prelabeled == 1 and result.remaining == 1


def test_prelabel_redis_unavailable_raises_503(svc):
    adapter, service = svc
    adapter.redis_ok = False
    with pytest.raises(ReviewUpstreamUnavailableError):
        service.prelabel(50)


# ==================== 最新事件拉取 ====================

def test_refresh_events_fetches_saves_and_returns_counts(svc):
    adapter, service = svc
    adapter.fetch_events = [{"title": "A"}, {"title": "B"}]
    adapter.save_return = [501, 502]
    result = service.refresh_events()
    assert result == RefreshResult(fetched=2, new_drafts=2, skipped_reason=None)
    events, conn = adapter.save_calls[0]
    assert events == adapter.fetch_events and conn is adapter.conn
    assert adapter.conn.closed
    lock_key, token, ttl_ms = adapter.lock_acquires[0]
    assert lock_key == "events:refresh_lock" and ttl_ms == 120_000
    assert adapter.lock_releases == [(lock_key, token)]  # 释放携带同一次调用的 token


def test_refresh_events_lock_held_skips_fetch(svc):
    adapter, service = svc
    adapter.lock_acquire_ok = False
    result = service.refresh_events()
    assert result == RefreshResult(fetched=0, new_drafts=0, skipped_reason="locked")
    assert adapter.fetch_calls == 0 and adapter.save_calls == []
    assert adapter.lock_releases == []  # 未持锁不释放


def test_refresh_events_redis_unavailable_raises_503(svc):
    adapter, service = svc
    adapter.redis_ok = False
    with pytest.raises(ReviewUpstreamUnavailableError):
        service.refresh_events()
    assert adapter.fetch_calls == 0


def test_refresh_events_fetch_error_degrades_and_releases_lock(svc):
    adapter, service = svc
    adapter.fetch_error = RuntimeError("network down")
    result = service.refresh_events()
    assert result == RefreshResult(fetched=0, new_drafts=0, skipped_reason="failed")
    assert adapter.save_calls == []
    assert adapter.lock_releases == [adapter.lock_acquires[0][:2]]


def test_refresh_events_save_error_degrades_closes_conn_releases_lock(svc):
    adapter, service = svc
    adapter.fetch_events = [{"title": "A"}]
    adapter.save_error = RuntimeError("pg down")
    result = service.refresh_events()
    assert result == RefreshResult(fetched=0, new_drafts=0, skipped_reason="failed")
    assert adapter.conn.closed
    assert adapter.lock_releases == [adapter.lock_acquires[0][:2]]


# ==================== 批量提交 ====================

def test_submit_batch_approve_fields_only_non_none_keys_and_compute_ok(svc):
    adapter, service = svc
    row = ReviewRowCommand(
        draft_id=1, action="approve", event_type="宏观数据", importance=5,
        expected_value=1.9, actual_value=None, previous_value=None,
        operator="tester",
    )
    result = service.submit_batch([row])
    conn, draft_id, fields, operator = adapter.approve_calls[0]
    assert draft_id == 1 and operator == "tester"
    assert fields == {"event_type": "宏观数据", "importance": 5, "expected_value": 1.9}  # 不含 None 键
    assert adapter.compute_calls == [(adapter.conn, 101)]
    assert result.approved == 1 and result.ignored == 0 and result.computed == 1
    row_result = result.results[0]
    assert row_result.ok and row_result.event_id == 101 and row_result.compute_status == "ok"
    assert adapter.conn.closed


def test_submit_batch_importance_none_key_omitted_delegates_default_chain(svc):
    adapter, service = svc
    result = service.submit_batch([ReviewRowCommand(draft_id=1, action="approve", importance=None)])
    _, _, fields, _ = adapter.approve_calls[0]
    assert "importance" not in fields  # 键缺失 → review_dao 取 importance_hint 或 3


def test_submit_batch_ignore_skips_compute(svc):
    adapter, service = svc
    result = service.submit_batch([ReviewRowCommand(draft_id=2, action="ignore")])
    assert adapter.compute_calls == []
    assert result.ignored == 1 and result.computed == 0
    assert result.results[0].compute_status == "skipped"


def test_submit_batch_row_value_error_isolated_and_rollback(svc):
    adapter, service = svc
    adapter.approve_errors[2] = ValueError("待审草稿不存在或已过期: 2")
    rows = [
        ReviewRowCommand(draft_id=1, action="approve"),
        ReviewRowCommand(draft_id=2, action="approve"),
        ReviewRowCommand(draft_id=3, action="ignore"),
    ]
    result = service.submit_batch(rows)
    assert [r.ok for r in result.results] == [True, False, True]
    failed = result.results[1]
    assert failed.error_code == "REVIEW_DRAFT_NOT_FOUND" and "不存在" in failed.error_message
    assert adapter.conn.rollbacks == 1  # 失败行 rollback，后续行继续
    assert result.approved == 1 and result.ignored == 1


def test_submit_batch_row_generic_error_mapped_row_failed(svc):
    adapter, service = svc
    adapter.approve_errors[1] = RuntimeError("DataError: invalid timestamptz")
    result = service.submit_batch([
        ReviewRowCommand(draft_id=1, action="approve"),
        ReviewRowCommand(draft_id=2, action="approve"),
    ])
    failed, ok_row = result.results
    assert failed.ok is False and failed.error_code == "REVIEW_ROW_FAILED"
    assert "DataError" in failed.error_message
    assert ok_row.ok and ok_row.compute_status == "ok"  # 后续行未受毒化


def test_submit_batch_compute_failure_keeps_row_ok_and_rollback(svc):
    adapter, service = svc
    adapter.compute_error = RuntimeError("OLS failed")
    result = service.submit_batch([ReviewRowCommand(draft_id=1, action="approve")])
    row_result = result.results[0]
    assert row_result.ok and row_result.compute_status == "failed"
    assert adapter.conn.rollbacks == 1  # compute 失败路径也 rollback
    assert result.approved == 1 and result.computed == 0


def test_submit_batch_approve_all_windows_failed_marks_failed(svc):
    adapter, service = svc
    adapter.compute_return = {
        "event_id": 101,
        "assets": {"000001.SH": {"pre_event_5d": {"error": "no data"}}},
    }
    result = service.submit_batch([ReviewRowCommand(draft_id=1, action="approve")])
    row_result = result.results[0]
    assert row_result.ok and row_result.compute_status == "failed"  # 与补算端点同一判定
    assert result.computed == 0


def test_submit_batch_approve_no_windows_marks_failed(svc):
    adapter, service = svc
    adapter.compute_return = {"event_id": 101, "assets": {}}  # 资产未初始化：什么都没算
    result = service.submit_batch([ReviewRowCommand(draft_id=1, action="approve")])
    assert result.results[0].compute_status == "failed"


def test_submit_batch_scope_precheck_failed_maps_row_failed_and_rollback(svc):
    """作用域前置校验失败：行级 REVIEW_ROW_FAILED（非 DRAFT_NOT_FOUND），rollback 后继续。"""
    adapter, service = svc
    adapter.scope_errors = ["sector 作用域至少需要一个目标引用", None]
    rows = [
        ReviewRowCommand(draft_id=1, action="approve", event_scope="sector"),
        ReviewRowCommand(draft_id=2, action="approve", event_scope="sector",
                         affected_scope_refs=["SW:801080"]),
    ]
    result = service.submit_batch(rows)
    failed, ok_row = result.results
    assert failed.ok is False
    assert failed.error_code == "REVIEW_ROW_FAILED"
    assert failed.error_code != "REVIEW_DRAFT_NOT_FOUND"
    assert "至少需要一个目标引用" in failed.error_message
    assert [c[1] for c in adapter.approve_calls] == [2]  # 失败行未进 approve
    assert adapter.conn.rollbacks == 1  # 校验失败也 rollback，避免毒化后续行
    assert ok_row.ok and result.approved == 1


def test_submit_batch_scope_ref_count_deduped_before_limit(svc):
    """引用数量按**去重后**条数判定（评审残留）：21 条重复引用不得误伤该行。

    同一目标写两次（多来源表单/拼接）经 `normalize_scope_refs` 去重后只有 1 个
    目标；按原始条数判定会把合法输入挡在行级失败上。
    """
    adapter, service = svc
    result = service.submit_batch([
        ReviewRowCommand(draft_id=1, action="approve", event_scope="sector",
                         affected_scope_refs=["SW:801080"] * 21),
    ])
    assert result.results[0].ok, result.results[0].error_message
    assert result.approved == 1
    assert [c[1] for c in adapter.approve_calls] == [1]

    # 去重后仍超上限（21 个不同目标）→ 行级 REVIEW_ROW_FAILED，且不进 approve
    distinct = [f"SW:80{n:04d}" for n in range(1000, 1021)]
    result = service.submit_batch([
        ReviewRowCommand(draft_id=2, action="approve", event_scope="sector",
                         affected_scope_refs=distinct),
    ])
    failed = result.results[0]
    assert failed.ok is False
    assert failed.error_code == "REVIEW_ROW_FAILED"
    assert failed.error_code != "REVIEW_DRAFT_NOT_FOUND"
    assert "上限" in failed.error_message
    assert [c[1] for c in adapter.approve_calls] == [1]  # 失败行未进 approve
    # 长度/数量校验是纯内存前置检查（未执行语句）→ 无需 rollback
    assert adapter.conn.rollbacks == 0


def test_submit_batch_scope_precheck_receives_route_fields(svc):
    """前置校验拿到归一前的路由字段（DAO 负责归一；None 键不传，交默认链）。"""
    adapter, service = svc
    service.submit_batch([
        ReviewRowCommand(draft_id=1, action="approve", event_scope="sector",
                         affected_scope_refs=["801080", "BK1753.DC"]),
        ReviewRowCommand(draft_id=2, action="approve", event_scope="market"),
        ReviewRowCommand(draft_id=3, action="approve"),  # 未携带路由字段（旧前端）
    ])
    assert adapter.scope_calls[0][1]["event_scope"] == "sector"
    assert adapter.scope_calls[0][1]["affected_scope_refs"] == ["801080", "BK1753.DC"]
    assert adapter.scope_calls[1][1]["event_scope"] == "market"
    assert "affected_scope_refs" not in adapter.scope_calls[1][1]
    assert "event_scope" not in adapter.scope_calls[2][1]  # 缺字段 → DAO 回退 market
    assert len(adapter.approve_calls) == 3


def test_submit_batch_ignore_skips_scope_precheck(svc):
    adapter, service = svc
    service.submit_batch([ReviewRowCommand(draft_id=1, action="ignore", event_scope="sector")])
    assert adapter.scope_calls == []  # ignored 不参与检索，不做路由校验


def test_submit_batch_unknown_action_defensive_row_failed(svc):
    adapter, service = svc
    result = service.submit_batch([ReviewRowCommand(draft_id=1, action="bogus")])
    assert result.results[0].ok is False
    assert result.results[0].error_code == "REVIEW_ROW_FAILED"
    assert adapter.approve_calls == [] and adapter.ignore_calls == []


def test_submit_batch_redis_unavailable_raises_503_before_conn(svc):
    adapter, service = svc
    adapter.redis_ok = False
    with pytest.raises(ReviewUpstreamUnavailableError):
        service.submit_batch([ReviewRowCommand(draft_id=1, action="approve")])
    assert adapter.open_count == 0


# ==================== 补算 ====================

def test_compute_for_event_ok(svc):
    adapter, service = svc
    result = service.compute_for_event(7, operator="admin")
    assert result.status == "ok" and result.event_id == 7
    assert adapter.compute_calls == [(adapter.conn, 7)]
    assert adapter.conn.closed


def test_compute_for_event_value_error_maps_404(svc):
    adapter, service = svc
    adapter.compute_error = ValueError("事件 99 不存在")
    with pytest.raises(ReviewEventNotFoundError):
        service.compute_for_event(99, operator="admin")


def test_compute_for_event_generic_error_maps_500(svc):
    adapter, service = svc
    adapter.compute_error = RuntimeError("provider down")
    with pytest.raises(ReviewComputeFailedError):
        service.compute_for_event(7, operator="admin")


def test_compute_for_event_all_windows_failed_returns_200_failed(svc):
    adapter, service = svc
    adapter.compute_return = {
        "event_id": 7,
        "assets": {"000001.SH": {"pre_event_5d": {"error": "no data"}, "event_day": {"error": "no data"}}},
    }
    result = service.compute_for_event(7, operator="admin")
    assert result.status == "failed" and "兜底" in (result.message or "")


def test_compute_for_event_partial_errors_still_ok(svc):
    adapter, service = svc
    adapter.compute_return = {
        "event_id": 7,
        "assets": {"000001.SH": {"pre_event_5d": {"error": "x"}, "event_day": {"cumulative_abnormal_return": 0.1}}},
    }
    assert service.compute_for_event(7, operator="admin").status == "ok"


def test_compute_for_event_no_windows_returns_failed(svc):
    adapter, service = svc
    adapter.compute_return = {"event_id": 7, "assets": {}}  # 资产未初始化：不误报成功
    result = service.compute_for_event(7, operator="admin")
    assert result.status == "failed" and "资产" in (result.message or "")


# ==================== 影响草稿与确认 ====================

def test_list_impact_drafts_joins_title_and_conn_closed(svc):
    adapter, service = svc
    adapter.impact_drafts = [
        {"event_id": 7, "t0": "2026-08-03", "computed_at": "t", "assets": {"000001.SH": {}}},
        {"event_id": 8},
    ]
    adapter.titles = {7: "CPI 公布"}
    items = service.list_impact_drafts()
    assert items[0].event_id == 7 and items[0].title == "CPI 公布"
    assert items[0].assets == {"000001.SH": {}}
    assert items[1].assets == {}  # 缺 assets 兜底
    assert adapter.conn.closed


def test_confirm_impacts_passes_through_and_returns_count(svc):
    adapter, service = svc
    adapter.confirm_return = 3
    result = service.confirm_impacts(7, ["000001.SH", "000300.SH"], operator="tester")
    conn, event_id, tickers, operator = adapter.confirm_calls[0]
    assert event_id == 7 and tickers == ["000001.SH", "000300.SH"] and operator == "tester"
    assert result.inserted == 3
    assert adapter.conn.closed


def test_confirm_impacts_draft_missing_maps_404(svc):
    adapter, service = svc
    adapter.confirm_error = ValueError("影响草稿不存在或已过期: event_id=7（可重算）")
    with pytest.raises(ReviewDraftNotFoundError):
        service.confirm_impacts(7, ["000001.SH"], operator="admin")

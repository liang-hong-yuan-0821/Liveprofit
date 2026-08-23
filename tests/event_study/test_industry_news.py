"""
产业政策新闻事件流模块单测（方案 3.3.3）

- _win_bounds：右开界窗口 [curr-7d, curr+1d)，排除未来事件
- _filter_pending_drafts：窗口/过滤阈值/排序/截断；
  importance_hint=None + ai_suggestions={} 不抛错
- _merge_dedupe：approved 优先、标题前 20 字去重、总条数上限
- _format_section：可用串以 # 开头、单边不可用注记、全不可用串不以 # 开头
- fetch_recent_industry_events：monkeypatch PG/Redis 的组合降级路径
- interface wrapper：异常 → 不可用串（不以 # 开头）
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from AI.eventStudy.integration import industry_news as M

CST = timezone(timedelta(hours=8))

CURR = "2026-08-19"


def _dt(s):
    """'YYYY-MM-DD HH:MM' → tz-aware datetime（CST）"""
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=CST)


def _ev(title, at, importance=4, event_type="产业政策", content="", source=""):
    return {"title": title, "content": content, "announced_at": at,
            "importance": importance, "event_type": event_type, "source": source,
            "from": "approved"}


def _draft(title, at, importance_hint=None, ai=None, content=""):
    return {"title": title, "content": content, "announced_at": at,
            "importance_hint": importance_hint, "ai_suggestions": ai or {},
            "source": "金十数据快讯"}


# ==================== 时间窗口 ====================

def test_win_bounds_right_open():
    start, end = M._win_bounds(CURR, 7)
    assert start == _dt("2026-08-12 00:00")
    assert end == _dt("2026-08-20 00:00")   # curr+1 天右开界
    assert start <= _dt("2026-08-19 23:59") < end


def test_win_bounds_invalid_date():
    assert M._win_bounds("not-a-date", 7) == (None, None)
    assert M._win_bounds(None, 7) == (None, None)


# ==================== 待审草稿过滤 ====================

def test_filter_pending_drafts_window_and_threshold():
    win_start, win_end = M._win_bounds(CURR, 7)
    drafts = [
        _draft("产业政策事件", "2026-08-18T10:00", ai={"event_type": "产业政策",
                                                   "importance": 3}),
        _draft("重大宏观事件", "2026-08-18T11:00", importance_hint=5),
        _draft("普通公司新闻", "2026-08-18T12:00", importance_hint=2,
               ai={"event_type": "公司"}),
        _draft("窗口外事件", "2026-08-01T10:00", importance_hint=5),
        _draft("未来事件", "2026-08-21T10:00", importance_hint=5),   # t0 纪律：排除
    ]

    events, truncated = M._filter_pending_drafts(drafts, win_start, win_end)

    assert [e["title"] for e in events] == ["重大宏观事件", "产业政策事件"]  # 重要性降序
    assert truncated is False


def test_filter_pending_drafts_none_never_raises():
    """importance_hint=None + ai_suggestions={} → 不抛 TypeError（None 兜底）"""
    win_start, win_end = M._win_bounds(CURR, 7)
    drafts = [
        _draft("无星级无预填", "2026-08-18T10:00", importance_hint=None, ai={}),
        _draft("AI 预填产业政策", "2026-08-18T11:00",
               ai={"event_type": "产业政策"}),
    ]

    events, _ = M._filter_pending_drafts(drafts, win_start, win_end)

    assert [e["title"] for e in events] == ["AI 预填产业政策"]  # 无星级无预填落选
    assert events[0]["importance"] == 3    # 缺省 3 兜底


def test_filter_pending_drafts_truncation_flag():
    win_start, win_end = M._win_bounds(CURR, 7)
    drafts = [_draft(f"事件{i}", "2026-08-18T10:00",
                     importance_hint=5) for i in range(M.MAX_PER_SOURCE + 5)]

    events, truncated = M._filter_pending_drafts(drafts, win_start, win_end)

    assert len(events) == M.MAX_PER_SOURCE
    assert truncated is True


# ==================== 合并去重 ====================

def test_merge_dedupe_approved_priority():
    title = "相同的标题前缀" * 3
    approved = [_ev(title, _dt("2026-08-18 10:00"))]
    pending = [_ev(title, _dt("2026-08-18 11:00"))]
    pending[0]["from"] = "pending"

    merged = M._merge_dedupe(approved, pending)

    assert len(merged) == 1
    assert merged[0]["from"] == "approved"      # approved 优先保留


def test_merge_dedupe_time_desc_and_cap():
    events = [_ev(f"事件{i}", _dt(f"2026-08-1{min(i, 9)} 10:00"))
              for i in range(M.MAX_TOTAL + 5)]
    merged = M._merge_dedupe(events, [])
    assert len(merged) == M.MAX_TOTAL
    times = [e["announced_at"] for e in merged]
    assert times == sorted(times, reverse=True)


# ==================== 格式化 ====================

def test_fmt_source_url_domain():
    """URL 来源取域名，普通来源原样"""
    assert M._fmt_source("https://www.cls.cn/detail/123") == "www.cls.cn"
    assert M._fmt_source("http://example.com/a/b") == "example.com"
    assert M._fmt_source("金十数据快讯") == "金十数据快讯"
    assert M._fmt_source("") == ""


def test_format_section_no_summary_placeholder_and_source():
    """content 为空 → '（无摘要）'占位；approved 的 source_url 以域名展示"""
    merged = [{"title": "无摘要事件", "content": "", "announced_at": _dt("2026-08-18 10:00"),
               "importance": 4, "event_type": "产业政策",
               "source": "https://www.cls.cn/detail/1", "from": "approved"}]
    text = M._format_section(merged, 7)
    assert "（无摘要）" in text
    assert "www.cls.cn" in text


def test_format_section_starts_with_hash_and_notes():
    merged = [_ev("利好政策", _dt("2026-08-18 10:00"), event_type="产业政策",
                  content="摘要内容")]
    text = M._format_section(merged, 7, pg_err="PG挂了", redis_err=None)

    assert text.startswith("#")
    assert "已审核事件（1 条）" in text
    assert "待审核快讯（0 条" in text
    assert "利好政策" in text and "产业政策" in text
    assert "PG 事件库不可用（PG挂了）" in text


def test_format_section_pending_ai_prelabel_tag():
    merged = [{"title": "AI预填事件", "content": "", "announced_at": _dt("2026-08-18 10:00"),
               "importance": 4, "event_type": "产业政策", "source": "金十数据快讯",
               "from": "pending"}]
    text = M._format_section(merged, 7)
    assert "产业政策/AI预填" in text
    assert "未经人工确认" in text
    assert "（无摘要）" in text                  # pending 分支的无摘要占位


# ==================== 端到端降级路径 ====================

def test_fetch_pg_down_pending_only(monkeypatch):
    """PG 异常 → 仅待审快讯分支，输出仍以 # 开头"""
    monkeypatch.setattr(M, "_fetch_approved",
                        lambda win_start, win_end: (None, "connection refused"))
    monkeypatch.setattr(M, "is_redis_available", lambda: True)
    monkeypatch.setattr(M.review_dao, "get_pending_events",
                        lambda: [_draft("AI预填产业政策", "2026-08-18T10:00",
                                        ai={"event_type": "产业政策", "importance": 4})])

    text = M.fetch_recent_industry_events(CURR)

    assert text.startswith("#")
    assert "待审核快讯（1 条" in text
    assert "PG 事件库不可用（connection refused）" in text


def test_fetch_all_unavailable_returns_non_hash(monkeypatch):
    """PG 异常 + Redis 不可用 → 不可用串（不以 # 开头）"""
    monkeypatch.setattr(M, "_fetch_approved",
                        lambda win_start, win_end: (None, "PG down"))
    monkeypatch.setattr(M, "is_redis_available", lambda: False)

    text = M.fetch_recent_industry_events(CURR)

    assert not text.startswith("#")
    assert "数据不可用" in text
    assert "PG down" in text and "Redis 不可用" in text


def test_fetch_no_events_in_window(monkeypatch):
    """两侧可用但窗口内无命中事件 → 提示串（不以 # 开头）"""
    monkeypatch.setattr(M, "_fetch_approved", lambda win_start, win_end: ([], None))
    monkeypatch.setattr(M, "is_redis_available", lambda: True)
    monkeypatch.setattr(M.review_dao, "get_pending_events", lambda: [])

    text = M.fetch_recent_industry_events(CURR)

    assert not text.startswith("#")
    assert "无产业政策/重大行业事件" in text


def test_fetch_invalid_date(monkeypatch):
    text = M.fetch_recent_industry_events("bad-date")
    assert not text.startswith("#")
    assert "日期参数非法" in text


def test_fetch_approved_truncation_detected(monkeypatch):
    """approved 返回 MAX_PER_SOURCE+1 条 → 截断注记 + 输出截回 MAX_PER_SOURCE（严格 > 判定）"""
    rows = [{"title": f"事件{i}", "content": "", "announced_at": _dt("2026-08-18 10:00"),
             "importance": 4, "event_type": "产业政策", "source": ""}
            for i in range(M.MAX_PER_SOURCE + 1)]
    monkeypatch.setattr(M, "_fetch_approved", lambda ws, we: (rows, None))
    monkeypatch.setattr(M, "is_redis_available", lambda: True)
    monkeypatch.setattr(M.review_dao, "get_pending_events", lambda: [])

    text = M.fetch_recent_industry_events(CURR)

    assert text.startswith("#")
    assert f"共 {M.MAX_PER_SOURCE} 条" in text
    assert "已审核事件按重要性截前" in text


def test_fetch_approved_exact_limit_no_truncation_note(monkeypatch):
    """approved 恰好 MAX_PER_SOURCE 条 → 不出现截断注记（严格 > 判定）"""
    rows = [{"title": f"事件{i}", "content": "", "announced_at": _dt("2026-08-18 10:00"),
             "importance": 4, "event_type": "产业政策", "source": ""}
            for i in range(M.MAX_PER_SOURCE)]
    monkeypatch.setattr(M, "_fetch_approved", lambda ws, we: (rows, None))
    monkeypatch.setattr(M, "is_redis_available", lambda: True)
    monkeypatch.setattr(M.review_dao, "get_pending_events", lambda: [])

    text = M.fetch_recent_industry_events(CURR)

    assert "已审核事件按重要性截前" not in text


# ==================== interface wrapper ====================

def test_dataflow_wrapper_exception_returns_unavailable(monkeypatch):
    """wrapper 异常 → 不可用串（不以 # 开头），不向上抛"""
    from AI.dataflows import interface as dataflow

    def boom(curr_date):
        raise RuntimeError("boom")
    monkeypatch.setattr("AI.eventStudy.integration.industry_news.fetch_recent_industry_events",
                        boom)

    text = dataflow.get_industry_policy_news(CURR)

    assert not text.startswith("#")
    assert "数据不可用" in text and "boom" in text

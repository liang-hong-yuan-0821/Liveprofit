"""
板块层政策新闻事件流读取（板块层轮动战术与政策事件流方案 3.3）

从事件研究系统读取近 N 日产业政策/重大行业新闻：
- PG events 表（status='approved'，event_type='产业政策' OR importance>=阈值）
- Redis 待审草稿（ai_suggestions 预填分类过滤，未经人工确认）

合并去重后格式化为 Markdown 章节，供 dataflow.get_industry_policy_news
注入板块新闻分析师 prompt。

约定：
- 数据可用时输出以 "#" 开头；PG/Redis 全不可用时返回不以 "#" 开头的
  不可用串（对齐板块层门控契约风格）
- 时间窗口右开界 [curr_date - days_back, curr_date + 1 天)，排除未来事件
  （t0 纪律）；分析日当天全部事件可见（晚间分析时当日事件已全知）
- 本模块只读消费事件流，不改事件研究系统的爬虫/审核流程
"""

import logging
from datetime import datetime, timedelta, timezone

from AI.eventStudy.collectors.config import (
    parse_int_env, is_redis_available,
)
from AI.eventStudy.review import review_dao

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))  # 北京时间

# 回看窗口（自然日）
DEFAULT_DAYS_BACK = parse_int_env("EVENT_STUDY_INDUSTRY_NEWS_DAYS", 7)
# 单源条数上限
MAX_PER_SOURCE = parse_int_env("EVENT_STUDY_INDUSTRY_NEWS_PER_SOURCE", 20)
# 合并后总条数上限
MAX_TOTAL = parse_int_env("EVENT_STUDY_INDUSTRY_NEWS_MAX_TOTAL", 30)
# 纳入阈值（重要性 >= MIN_IMPORTANCE 或 event_type='产业政策'）
MIN_IMPORTANCE = parse_int_env("EVENT_STUDY_INDUSTRY_NEWS_MIN_IMPORTANCE", 4)

_APPROVED_SQL = """
SELECT title, content, event_type, event_subtype, event_condition,
       importance, announced_at, source_url
FROM events
WHERE status = 'approved'
  AND announced_at >= %s::timestamptz AND announced_at < %s::timestamptz
  AND (event_type = '产业政策' OR importance >= %s)
ORDER BY importance DESC, announced_at DESC
LIMIT %s
"""


def _win_bounds(curr_date: str, days_back: int):
    """时间窗口 [curr_date - days_back, curr_date + 1 天)（Asia/Shanghai tz-aware）。

    curr_date: 'YYYY-MM-DD'（分析交易日）。返回 (win_start, win_end) datetime。
    解析失败返回 (None, None)（调用方按全不可用处理）。
    """
    try:
        day = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d").replace(tzinfo=CST)
    except (TypeError, ValueError):
        logger.warning(f"产业政策新闻: 非法日期 {curr_date!r}")
        return None, None
    return day - timedelta(days=days_back), day + timedelta(days=1)


def _norm_event(title: str, content: str, announced_at, importance,
                event_type: str, source: str) -> dict:
    """统一事件结构（PG 行 / Redis 草稿共用）。announced_at 为 tz-aware datetime。"""
    return {
        "title": (title or "").strip(),
        "content": (content or "").strip(),
        "announced_at": announced_at,
        "importance": int(importance) if importance is not None else 3,
        "event_type": (event_type or "").strip() or "未分类",
        "source": (source or "").strip(),
    }


def _parse_pending_time(raw) -> datetime:
    """待审草稿 announced_at（ISO8601）→ tz-aware datetime；失败返回 None。"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt.astimezone(CST)
    except (ValueError, TypeError):
        return None


def _fetch_approved(win_start, win_end) -> tuple:
    """PG approved 分支：返回 (events, err)。events 为 _norm_event 列表；
    PG 连接/查询失败 → (None, err 原因串)。"""
    from AI.eventStudy.db.connection import get_connection
    try:
        conn = get_connection()
    except Exception as e:
        logger.warning(f"产业政策新闻: PG 连接失败: {e}")
        return None, str(e)
    try:
        # LIMIT 多取 1 条探测截断（LIMIT 满额 ≠ 截断，严格判定需要 +1）
        rows = conn.execute(_APPROVED_SQL, (win_start, win_end, MIN_IMPORTANCE,
                                            MAX_PER_SOURCE + 1)).fetchall()
    except Exception as e:
        logger.warning(f"产业政策新闻: PG 查询失败: {e}")
        return None, str(e)
    finally:
        conn.close()
    # SELECT 列序：title, content, event_type, event_subtype, event_condition,
    # importance, announced_at, source_url
    events = [_norm_event(r[0], r[1], r[6], r[5], r[2], r[7] or "") for r in rows]
    return events, None


def _filter_pending_drafts(drafts, win_start, win_end) -> tuple:
    """Redis 待审草稿过滤：返回 (events, truncated)。

    None 兜底为显式要求：importance_hint 常为 None（无星级）、ai_suggestions
    常为 {}，None >= 4 会抛 TypeError——过滤与排序一律 int(... or 0)/... or 3
    （沿用 review_app 的既有口径）。
    truncated = 截断前命中数超过 MAX_PER_SOURCE。
    """
    picked = []
    for d in drafts:
        dt = _parse_pending_time(d.get("announced_at"))
        if dt is None or not (win_start <= dt < win_end):
            continue
        sugg = d.get("ai_suggestions") or {}
        hint = d.get("importance_hint")
        hit = (sugg.get("event_type") == "产业政策"
               or int(sugg.get("importance") or 0) >= MIN_IMPORTANCE
               or int(hint or 0) >= MIN_IMPORTANCE)
        if not hit:
            continue
        importance = int(sugg.get("importance") or hint or 3)
        picked.append(_norm_event(
            d.get("title"), d.get("content"), dt, importance,
            sugg.get("event_type") or "", d.get("source") or ""))
    truncated = len(picked) > MAX_PER_SOURCE
    # 重要性降序、时间降序
    picked.sort(key=lambda e: (e["importance"], e["announced_at"]), reverse=True)
    return picked[:MAX_PER_SOURCE], truncated


def _merge_dedupe(approved, pending) -> list:
    """合并去重：标题前 20 字归一化为 key；approved 优先（pending 同 key 丢弃）。

    输入事件均带 "from" 标记（"approved"/"pending"）。
    返回时间降序、总条数 <= MAX_TOTAL 的列表。
    """
    merged = list(approved or [])
    seen = {e["title"][:20].strip() for e in merged}
    for e in pending or []:
        key = e["title"][:20].strip()
        if key in seen:
            continue
        seen.add(key)
        merged.append(e)
    merged.sort(key=lambda e: e["announced_at"], reverse=True)
    return merged[:MAX_TOTAL]


def _stars(n: int) -> str:
    n = max(1, min(5, int(n)))
    return "★" * n


def _fmt_time(dt) -> str:
    try:
        return dt.astimezone(CST).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _fmt_source(source: str) -> str:
    """来源展示：URL 取域名（避免整链过长），其余原样。"""
    s = (source or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        try:
            return s.split("/", 3)[2] or s
        except IndexError:
            return s
    return s


def _format_section(merged, n_days, pg_err=None, redis_err=None,
                    approved_truncated=False, pending_truncated=False) -> str:
    """格式化为 Markdown 章节（可用时以 "#" 开头）。"""
    approved_events = [e for e in merged if e["from"] == "approved"]
    pending_events = [e for e in merged if e["from"] == "pending"]
    lines = [
        f"# 近期产业政策/重大行业新闻（近 {n_days} 日，共 {len(merged)} 条；来源：事件研究系统）",
        "",
    ]

    lines.append(f"## 已审核事件（{len(approved_events)} 条）")
    if approved_events:
        for e in approved_events:
            src = f" {_fmt_source(e['source'])}" if e["source"] else ""
            lines.append(f"- [{_fmt_time(e['announced_at'])}] {_stars(e['importance'])} "
                         f"《{e['title']}》 [{e['event_type']}]{src}")
            if e["content"]:
                lines.append(f"  {e['content'][:100]}")
            else:
                lines.append("  （无摘要）")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append(f"## 待审核快讯（{len(pending_events)} 条，AI 预填分类，未经人工确认）")
    if pending_events:
        for e in pending_events:
            src = f" {e['source']}" if e["source"] else ""
            lines.append(f"- [{_fmt_time(e['announced_at'])}] {_stars(e['importance'])} "
                         f"《{e['title']}》 [{e['event_type']}/AI预填]{src}")
            if e["content"]:
                lines.append(f"  {e['content'][:100]}")
            else:
                lines.append("  （无摘要）")
    else:
        lines.append("（无）")
    lines.append("")

    notes = ["事件流来自事件研究系统（PG approved + Redis pending 合并去重）。",
             f"已按 产业政策或重要性≥{MIN_IMPORTANCE} 过滤；待审快讯含 AI 预填分类，可能有噪声。"]
    if pg_err:
        notes.append(f"PG 事件库不可用（{pg_err}），仅含待审快讯。")
    if redis_err:
        notes.append(f"Redis 草稿区不可用（{redis_err}），仅含已审核事件。")
    if approved_truncated:
        notes.append(f"已审核事件按重要性截前 {MAX_PER_SOURCE} 条。")
    if pending_truncated:
        notes.append(f"待审快讯按重要性截前 {MAX_PER_SOURCE} 条。")
    lines.extend(f"> {n}" for n in notes)
    return "\n".join(lines)


def fetch_recent_industry_events(curr_date: str, days_back: int = None) -> str:
    """近 N 日产业政策/重大行业新闻（PG approved + Redis pending 合并）。

    curr_date: 'YYYY-MM-DD'（分析交易日，即 state["trade_date"]）。
    返回 Markdown 章节（可用时以 "#" 开头）；PG/Redis 全不可用时返回不以
    "#" 开头的不可用串（对齐板块层门控契约风格）。
    """
    days_back = days_back or DEFAULT_DAYS_BACK
    win_start, win_end = _win_bounds(curr_date, days_back)
    if win_start is None:
        return "数据不可用：产业政策新闻获取失败（日期参数非法）。"

    # PG approved 分支（不可用不中断，单边降级）
    approved, pg_err = _fetch_approved(win_start, win_end)
    # _fetch_approved 多取 1 条探测：超过上限才判截断（与 pending 分支严格 > 口径一致）
    approved_truncated = bool(approved) and len(approved) > MAX_PER_SOURCE
    if approved_truncated:
        approved = approved[:MAX_PER_SOURCE]

    # Redis pending 分支（is_redis_available 显式探测，get_pending_events
    # 返回 [] 无法区分"草稿为空"与"Redis 不可用"）
    pending, redis_err, pending_truncated = [], None, False
    if is_redis_available():
        try:
            pending, pending_truncated = _filter_pending_drafts(
                review_dao.get_pending_events(), win_start, win_end)
        except Exception as e:
            logger.warning(f"产业政策新闻: 待审草稿读取失败: {e}")
            redis_err = str(e)
    else:
        redis_err = "Redis 不可用"

    # 全不可用（PG 异常 + Redis 不可用，无任何数据来源）
    if approved is None and redis_err:
        return (f"数据不可用：事件研究系统不可达（PG: {pg_err}；Redis: {redis_err}），"
                f"无法获取近期产业政策/行业新闻。")

    merged = _merge_dedupe(
        [dict(e, **{"from": "approved"}) for e in (approved or [])],
        [dict(e, **{"from": "pending"}) for e in pending],
    )
    if not merged:
        return (f"数据不可用：近 {days_back} 日无产业政策/重大行业事件"
                f"（PG: {pg_err or '可用'}；Redis: {redis_err or '可用'}）。")
    return _format_section(merged, days_back,
                           pg_err=pg_err, redis_err=redis_err,
                           approved_truncated=approved_truncated,
                           pending_truncated=pending_truncated)

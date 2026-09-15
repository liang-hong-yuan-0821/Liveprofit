"""
三级事件研究预取公共原语（T4；方案第三、五章）

市场 / 板块 / 个股三个预取器（`AI/marketAgents/analysts/international_event_prefetch.py`、
`AI/sectorAgents/analysts/sector_event_prefetch.py`、`AI/stockAgents/analysts/stock_event_prefetch.py`）
同构复用本模块的原语：

- 候选时点校验：仅接受 `event_time <= trade_date`；日期精度不足（无时刻）**保守排除同日**
  （防前视——同日的无时刻事件无法判定是否已在分析时点前发生）
- 候选去重：ID 优先，其次「来源 + 标题 + 时间」，再按「标题 + 时间」跨来源合并
- 固定窗口逐条调用 `predict_impact(..., as_of=event_time, event_scope=..., scope_refs=...)`，
  单条失败降级（`api_unavailable` / `no_sample`），不抛异常、不阻塞上层节点
- 结果组装为 Prompt 数据块（`render_prefetch_block`；统计只来自预测器返回值）；
  结构化事件组装为同构字段（`candidate_to_event`）

设计约束（方案第五章「事件研究预取」）：
- 预取结果**仅作 Prompt 变量与结构化事件组装输入**（不进 State；State 只保留实际引用摘要）
- 统计不得由 LLM 补写：无样本/不可用时 `historical_impact=None` + 原因
- `conn` / `predict_fn` / `route_lister` 均可注入（默认取真实 PG 连接与预测器），
  单测用 fake 替代，不触网

本模块不做 LLM 调用、不写 State、不发起网络请求（除调用方给的预测器/查询函数外）。
"""

import hashlib
import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ==================== 常量（首期固定窗口，方案第五章） ====================

MAX_PREFETCH_CANDIDATES = 5              # 每层最多注入 5 条
DEFAULT_ASSET_TICKERS = ("000300.SH",)   # 首期固定沪深 300
DEFAULT_WINDOW_TYPE = "post_event_5d"    # 首期固定窗口
DEFAULT_ROUTE_LIMIT = 20                 # 路由事件读取条数上界
MAX_ROUTE_REFS = 40                      # 路由目标引用上限（板块扫描范围归一后）

SCOPE_MARKET = "market"
SCOPE_SECTOR = "sector"
SCOPE_STOCK = "stock"

# 预取整体状态 / 单条历史匹配状态（后者与 predict_impact.sample_metadata.history_match_status 对齐）
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_NO_SAMPLE = "no_sample"
STATUS_COVERAGE_MISSING = "coverage_missing"
STATUS_API_UNAVAILABLE = "api_unavailable"
STATUS_EMPTY = "empty"              # 无候选（或下层无命中目标）
STATUS_UNMATCHED = "unmatched"      # 结构化事件未匹配到预取候选（无历史统计可引用）

PRECISION_DATETIME = "datetime"
PRECISION_DATE = "date"
PRECISION_NONE = "none"
# 时点上界（trade_date）不可解析 → 无法判定可见性：候选进 filter_stats 的
# unparsed 桶单独计数（评审 m15；不再设独立精度常量，避免"已落标"假象）

_REASON_NO_SAMPLE = "事件库暂无该资产/作用域下的已审核样本"


# ==================== 连接与事务 ====================

def safe_rollback(conn) -> None:
    """尽力回滚：本地 PG 连接为 autocommit=False，失败语句会把事务置为 aborted，
    不回滚则后续所有语句以 "current transaction is aborted" 失败并被吞成
    api_unavailable（事务中毒连坐，评审 M4）。

    回滚自身失败（连接已断/None 等）不再抛出——调用点已在异常降级路径上。
    """
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 —— 回滚失败不覆盖原始失败原因
        pass


# ==================== 事件时点解析与防前视 ====================

_FULL_DATETIME = re.compile(
    r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"
    r"(?:\s+|T)(\d{1,2}):(\d{2})(?::(\d{2}))?"
)
_SHORT_DATETIME = re.compile(
    r"(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?"
)
_FULL_DATE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
# 短日期仅在「括号内」尝试（避免把正文里的「1-5 交易日」等区间误判为 1 月 5 日）；
# `-` / `/` 分隔另要求两位零填充（「08-08」是日期，「1-5」「5-20」是区间，评审 m15）
_SHORT_DATE = re.compile(
    r"(?P<m>\d{1,2})\s*(?P<sep>[-/月])\s*(?P<d>\d{1,2})\s*日?(?!\s*[\d:])")
_BRACKET = re.compile(r"\[([^\[\]]{2,32})\]")


def as_date(value) -> date | None:
    """'YYYY-MM-DD' / 'YYYYMMDD' / datetime / date → date；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt, length in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:length], fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _build_datetime(year, month, day, hour=0, minute=0, second=0) -> datetime | None:
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
    except (TypeError, ValueError):
        return None


def _infer_year(month, day, trade_date) -> int | None:
    """无年份短日期（财联社电报「08-08 09:30」）补年份：晚于交易日 1 天以上视为上一年。"""
    ref = as_date(trade_date)
    if ref is None:
        return None
    try:
        candidate = date(ref.year, int(month), int(day))
    except (TypeError, ValueError):
        return None
    return ref.year - 1 if (candidate - ref).days > 1 else ref.year


def _parse_fragment(text: str, trade_date, *, allow_short: bool):
    m = _FULL_DATETIME.search(text)
    if m:
        dt = _build_datetime(*m.groups()[:3], m.group(4), m.group(5), m.group(6) or 0)
        if dt:
            return dt, PRECISION_DATETIME
    if allow_short:
        m = _SHORT_DATETIME.search(text)
        if m:
            year = _infer_year(m.group(1), m.group(2), trade_date)
            if year:
                dt = _build_datetime(year, m.group(1), m.group(2),
                                     m.group(3), m.group(4), m.group(5) or 0)
                if dt:
                    return dt, PRECISION_DATETIME
    m = _FULL_DATE.search(text)
    if m:
        dt = _build_datetime(*m.groups()[:3])
        if dt:
            return dt, PRECISION_DATE
    if allow_short:
        for m in _SHORT_DATE.finditer(text):
            month, day = m.group("m"), m.group("d")
            # `-` / `/` 分隔要求两位零填充：「08-08」是日期，「1-5」「5-20」是
            # 区间（交易日/天数），不得当作 event_time（评审 m15）
            if m.group("sep") in ("-", "/") and not (len(month) >= 2 and len(day) >= 2):
                continue
            year = _infer_year(month, day, trade_date)
            if year:
                dt = _build_datetime(year, month, day)
                if dt:
                    return dt, PRECISION_DATE
    return None, PRECISION_NONE


def parse_event_time(raw, trade_date=None) -> tuple[datetime | None, str]:
    """解析事件时间 → (datetime, 精度)。精度 ∈ {"datetime", "date", "none"}。

    优先识别括号内的短时间（「[08-08 09:30] 标题」财联社电报形态，年份按
    trade_date 推断、跨年回退上一年）；其次全文含年份的完整形态。
    """
    if raw is None:
        return None, PRECISION_NONE
    if isinstance(raw, datetime):
        return raw, PRECISION_DATETIME
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day), PRECISION_DATE
    text = str(raw).strip()
    if not text:
        return None, PRECISION_NONE
    m = _BRACKET.search(text)
    if m:
        dt, precision = _parse_fragment(m.group(1), trade_date, allow_short=True)
        if dt is not None:
            return dt, precision
    return _parse_fragment(text, trade_date, allow_short=False)


def event_time_str(value, precision: str) -> str | None:
    """事件时间归一为字符串（datetime 精度保留时刻与时区偏移；仅日期精度只留日期）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if precision == PRECISION_DATETIME:
        return value.isoformat(timespec="seconds")
    return value.strftime("%Y-%m-%d")


def is_visible_at(value, precision: str, trade_date) -> bool:
    """时点校验：仅接受 `event_time <= trade_date`。

    日期精度不足（precision == "date"）时**保守排除同日**——无法判定该事件
    是否发生在分析时点之前，宁可漏取不可穿越。

    注意：`trade_date` 不可解析时无法判定可见性，本函数返回 `False`（保守），
    调用方须先用 `as_date(trade_date)` 区分「不可判定（unparsed）」与「晚于
    交易日（future）」，不得把不可判定静默计入 future（评审 m15）。
    """
    if value is None or precision == PRECISION_NONE:
        return False
    ref = as_date(trade_date)
    if ref is None:
        return False
    if precision == PRECISION_DATE:
        return value.date() < ref
    return value.date() <= ref


def filter_visible_candidates(candidates, trade_date) -> tuple[list[dict], dict]:
    """按 trade_date 过滤候选（防前视）。返回 (可见候选, 统计)。

    统计 dict：{"kept": n, "future": n, "undated": n, "unparsed": n,
    "unparsed_refs": [...]}——future = 晚于交易日；undated = 无任何可解析时间
    （时间未知，保守排除）；**unparsed = 时点上界 `trade_date` 不可解析致可见性
    不可判定**：保留原始 event_time 串单独计数（评审 m15），仍保守排除出 kept。
    """
    kept, future, undated, unparsed = [], 0, 0, []
    ref_parsed = as_date(trade_date) is not None
    for cand in candidates or []:
        dt, precision = parse_event_time(cand.get("event_time"), trade_date)
        if precision == PRECISION_NONE:
            undated += 1
            continue
        if not ref_parsed:
            unparsed.append(str(cand.get("event_time")))
            continue
        if not is_visible_at(dt, precision, trade_date):
            future += 1
            continue
        kept.append(cand)
    return kept, {
        "kept": len(kept),
        "future": future,
        "undated": undated,
        "unparsed": len(unparsed),
        "unparsed_refs": unparsed[:5],
    }


# ==================== 候选构造与去重 ====================

_MD_NOISE = re.compile(r"[\s*_`#>|\[\]【】()（）:：,，.。!！?？\"'“”‘’\-—…~]+")


def normalize_title(title) -> str:
    """标题归一（去空白与标点、截断 60 字）——去重键与同名匹配共用。"""
    return _MD_NOISE.sub("", str(title or ""))[:60]


def candidate_id(source: str, title: str) -> str:
    """候选稳定 ID（无事件库 ID 时按来源 + 标题哈希）。"""
    digest = hashlib.sha1(f"{source}|{title}".encode("utf-8")).hexdigest()[:12]
    return f"{source}:{digest}"


def build_candidate(source, title, event_time=None, trade_date=None, *,
                    summary="", source_url=None, event_id=None,
                    event_type=None, event_subtype=None, event_condition=None) -> dict:
    """构造候选（各层预取器的公共结构）。

    event_time 可传字符串（含时间文本的原文或 ISO）或 datetime；解析不到的
    时间记为 `time_precision="none"`，后续时点校验会保守排除。
    """
    dt, precision = parse_event_time(event_time, trade_date)
    return {
        "candidate_id": f"event:{event_id}" if event_id is not None else candidate_id(source, title),
        "event_id": event_id,
        "source": source,
        "source_url": source_url,
        "title": str(title or "").strip(),
        "summary": str(summary or "").strip(),
        "event_time": event_time_str(dt, precision) if dt is not None else (
            str(event_time) if event_time else None),
        "time_precision": precision,
        "event_type": event_type,
        "event_subtype": event_subtype,
        "event_condition": event_condition,
    }


def dedupe_candidates(candidates) -> list[dict]:
    """去重：ID 优先，其次「来源 + 标题 + 时间」，再按「标题 + 时间」跨来源合并。

    跨来源合并（第三条键）用于剔除同一事件由不同来源重复描述（如事件库行与
    新闻行同标题同时间）产生的重复条目；保序，保留首个出现者。
    """
    seen_ids, seen_keys, seen_title_times, out = set(), set(), set(), []
    for cand in candidates or []:
        title_key = normalize_title(cand.get("title"))
        if not title_key:
            continue
        cid = cand.get("candidate_id")
        key = (cand.get("source") or "", title_key, cand.get("event_time") or "")
        title_time = (title_key, cand.get("event_time") or "")
        if cid and cid in seen_ids:
            continue
        if key in seen_keys or title_time in seen_title_times:
            continue
        if cid:
            seen_ids.add(cid)
        seen_keys.add(key)
        seen_title_times.add(title_time)
        out.append(cand)
    return out


def _sort_key(cand):
    dt, precision = parse_event_time(cand.get("event_time"), None)
    if dt is None:
        return datetime.min
    return dt.replace(tzinfo=None)


def select_candidates(candidates, trade_date, max_candidates=MAX_PREFETCH_CANDIDATES):
    """时点过滤 → 去重 → 近事优先排序 → 截断（返回 (候选, 过滤统计)）。"""
    visible, stats = filter_visible_candidates(candidates, trade_date)
    deduped = dedupe_candidates(visible)
    deduped.sort(key=_sort_key, reverse=True)
    stats["deduped"] = max(0, len(visible) - len(deduped))
    stats["selected"] = min(len(deduped), max(1, int(max_candidates)))
    return deduped[:max_candidates], stats


# ==================== 自由文本候选抽取 ====================

_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.、)])\s*")
_STARS = re.compile(r"[★☆]+\s*")
_BOOK_TITLE = re.compile(r"^《(.+?)》")
_NUMERIC_CELL = re.compile(r"^[+\-]?[\d.,%％倍万亿年月日\s]*$")


_LEADING_BRACKET = re.compile(r"^\[([^\[\]]{2,32})\]\s*")


def _strip_leading_timestamp(text: str, trade_date=None) -> str:
    """去掉行首时间戳括号（「[08-08 09:30] 标题」财联社电报形态）。

    仅当括号内容可解析为事件时间时移除（时间已单独存为 `event_time`，标题与
    事实文本不应重复携带）；非时间括号（如「[重磅]」）原样保留。
    """
    match = _LEADING_BRACKET.match(text)
    if not match:
        return text
    dt, _ = _parse_fragment(match.group(1), trade_date, allow_short=True)
    if dt is None:
        return text
    return text[match.end():].strip()


def _clean_line_title(line: str, trade_date=None) -> str:
    text = _BULLET.sub("", str(line or "").strip())
    text = _STARS.sub("", text).strip()
    text = _strip_leading_timestamp(text, trade_date)
    if text.startswith("|"):
        cells = [c.strip() for c in text.strip("|").split("|")]
        cells = [c for c in cells
                 if c and not _NUMERIC_CELL.match(c) and not set(c) <= {"-", ":", " "}]
        text = max(cells, key=len) if cells else ""
    book = _BOOK_TITLE.match(text)
    if book:
        text = book.group(1).strip()
    return text.strip()


def extract_text_candidates(text, trade_date, source, *, max_items=40) -> list[dict]:
    """自由文本（新闻流/日历/指标）→ 候选列表（逐行取「可解析时间 + 标题」）。

    - 跳过标题行（`#`）、空行、分隔行；表格行取最长非数值单元格作标题
    - 时间为空的行也保留（`time_precision="none"`），由时点校验统计并保守排除
    - 标题过短（归一后 < 3 字）的行丢弃（表头/装饰行）
    """
    candidates = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if set(line) <= {"-", "|", ":", " "}:
            continue
        title = _clean_line_title(line, trade_date)
        if len(normalize_title(title)) < 3:
            continue
        dt, precision = parse_event_time(line, trade_date)
        candidates.append(build_candidate(
            source, title,
            event_time=event_time_str(dt, precision) if dt is not None else None,
            trade_date=trade_date,
            summary=title[:200],
        ))
        if len(candidates) >= max_items:
            break
    return candidates


# ==================== 依赖注入默认实现 ====================

def open_conn():
    """建立事件研究库 PG 连接（进程内直连）；失败返回 None（调用方降级为不可用）。"""
    try:
        from AI.eventStudy.db.connection import get_connection
        return get_connection()
    except Exception as e:
        logger.warning(f"事件研究库连接失败: {e}")
        return None


def close_conn(conn):
    try:
        conn.close()
    except Exception:
        pass


def _default_route_lister():
    try:
        from AI.eventStudy.review.review_dao import list_approved_events_for_route
        return list_approved_events_for_route
    except Exception as e:
        logger.warning(f"事件研究路由查询不可用: {e}")
        return None


def _default_predict_fn():
    try:
        from AI.eventStudy.prediction.predictor import predict_impact
        return predict_impact
    except Exception as e:
        logger.warning(f"事件研究预测器不可用: {e}")
        return None


def _normalize_scope(value):
    try:
        from AI.eventStudy.review.review_dao import normalize_scope
        scope = normalize_scope(value)
        if scope:
            return scope
    except Exception:
        pass
    text = str(value or "").strip().lower()
    return text if text in (SCOPE_MARKET, SCOPE_SECTOR, SCOPE_STOCK) else SCOPE_MARKET


def normalize_refs(scope_refs):
    """目标引用归一（复用审核侧同一规则）；无法识别者丢弃（不静默落到全量事件）。"""
    raw = list(scope_refs or [])
    try:
        from AI.eventStudy.review.review_dao import normalize_scope_ref
        return [r for r in (normalize_scope_ref(item) for item in raw) if r]
    except Exception:
        return [str(r).strip() for r in raw if str(r).strip()]


def normalize_route_refs(scope_refs, limit=MAX_ROUTE_REFS) -> tuple[list[str], dict]:
    """路由目标引用归一 + **统一截断**（评审 M10）——候选侧与预测侧的单点入口。

    预取器（板块/个股）先用本函数取 refs，再**同时**用于 `EventRoute` 构造与
    `run_prefetch(scope_refs=refs)`；此前候选侧按未截断目标扫描、预测侧按
    `[:MAX_ROUTE_REFS]` 截断后统计，两侧目标集合不一致会污染历史统计。

    Returns:
        `(refs, meta)`；meta = {"input": 归一前条数, "kept": 截断后条数,
        "truncated": 截断丢弃条数, "dropped": 无法识别丢弃条数}。
        `truncated > 0` 时调用方须把截断事实计入覆盖统计（不得静默）。
    """
    raw = list(scope_refs or [])
    normalized = normalize_refs(raw)
    if limit is None:
        kept = normalized
    else:
        kept = normalized[:max(0, int(limit))]
    return kept, {
        "input": len(raw),
        "kept": len(kept),
        "truncated": len(normalized) - len(kept),
        "dropped": len(raw) - len(normalized),
    }


def fetch_route_candidates(conn, route, trade_date, *, limit=DEFAULT_ROUTE_LIMIT,
                           route_lister=None, source_label="事件库"):
    """路由正式事件 → 候选（按 announced_at 作事件时间）。返回 (候选, 错误原因)。

    依赖注入：`route_lister` 默认 `review_dao.list_approved_events_for_route`
    （固定 approved + `announced_at <= as_of`）。查询失败不抛异常，返回空列表 + 原因。
    """
    if conn is None:
        return [], "事件研究库连接不可用"
    lister = route_lister or _default_route_lister()
    if lister is None:
        return [], "事件研究路由查询不可用"
    try:
        rows = lister(conn, route, trade_date, limit=limit)
    except Exception as e:
        # 查询失败同样会中毒事务（评审 M4 残留）：先回滚，避免连坐后续查询
        safe_rollback(conn)
        logger.warning(f"路由事件查询失败: {e}")
        return [], f"路由事件查询失败: {e}"
    candidates = []
    for row in rows or []:
        candidates.append(build_candidate(
            source_label,
            row.get("title") or "",
            event_time=row.get("announced_at"),
            trade_date=trade_date,
            summary=(row.get("content") or "")[:200],
            source_url=row.get("source_url"),
            event_id=row.get("event_id"),
            event_type=row.get("event_type"),
            event_subtype=row.get("event_subtype"),
            event_condition=row.get("event_condition"),
        ))
    return candidates, None


# ==================== 逐条预测与降级 ====================

def _history_from_result(result, asset_ticker, window_type) -> tuple[str, dict | None, str | None]:
    """预测器返回 → (单条状态, historical_impact, 原因)。统计只来自返回值。"""
    meta = (result or {}).get("sample_metadata") or {}
    status = meta.get("history_match_status") or STATUS_NO_SAMPLE
    sample_count = meta.get("sample_count") or 0
    if status == STATUS_OK and sample_count > 0:
        prediction = (result or {}).get("prediction") or {}
        template_stats = (result or {}).get("template_stats") or {}
        impact = {
            "asset_ticker": asset_ticker,
            "window_type": window_type,
            "sample_count": sample_count,
            "template_sample_count": meta.get("template_sample_count"),
            "supplement_sample_count": meta.get("supplement_sample_count"),
            "avg_car": template_stats.get("avg_car"),
            "weighted_car": prediction.get("predicted_return"),
            "predicted_direction": prediction.get("predicted_direction"),
            "win_rate": template_stats.get("win_rate"),
            "confidence": prediction.get("confidence"),
            "contaminated": bool(meta.get("contaminated")),
            "contaminated_sample_count": meta.get("contaminated_sample_count"),
            "as_of": meta.get("as_of"),
            "note": (result or {}).get("note") or None,
            "reason": None,
        }
        # 通道部分失败/样本污染 → 单条状态 partial（统计仍可用）
        if meta.get("contaminated") or meta.get("unavailable_channels"):
            return STATUS_PARTIAL, impact, None
        return STATUS_OK, impact, None
    return status, None, meta.get("reason") or _REASON_NO_SAMPLE


def _aggregate_status(entries, *, first_error=None) -> tuple[str, str | None]:
    """整体状态聚合（三态可区分，评审 m16；首错原因，评审 M4）。

    - 全条目查询失败 → `api_unavailable`（附首个异常原因，区别于连接/预测器
      层「本身不可用」）
    - 至少一条有统计 → `ok` / `partial`
    - 无统计且**覆盖不足**（资产未登记等）→ `coverage_missing`
      （不得并入 `no_sample`：覆盖不足 ≠ 事件库确无样本，评审 m16）
    - 无统计且逐条均已核对 → `no_sample`
    """
    if not entries:
        return STATUS_EMPTY, "无候选事件（预取输入为空或时点校验后无剩余候选）"
    with_stats = [e for e in entries if e.get("historical_impact")]
    statuses = [e.get("history_match_status") for e in entries]
    unavailable = [s for s in statuses if s == STATUS_API_UNAVAILABLE]
    coverage = [s for s in statuses if s == STATUS_COVERAGE_MISSING]
    if with_stats:
        status = STATUS_OK if len(with_stats) == len(entries) else STATUS_PARTIAL
        # 部分候选失败时首错原因必须透出（评审残留）：status/reason 语义为
        # 「原因非空 = 有可核对的失败」，docstring 与实际一致
        return status, (first_error or None)
    if len(unavailable) == len(entries):
        reason = "事件研究查询不可用（无任何候选取得统计）"
        if first_error:
            reason += f"；首个失败原因: {first_error}"
        return STATUS_API_UNAVAILABLE, reason
    if coverage:
        reason = (f"事件研究覆盖不足（{len(coverage)}/{len(entries)} 条候选资产未登记），"
                  "不得据此断言事件库无样本")
        if first_error:
            reason += f"；部分候选查询失败: {first_error}"
        return STATUS_COVERAGE_MISSING, reason
    reason = "预取到候选但事件库无同层已审核样本"
    if first_error:
        reason += f"；部分候选查询失败: {first_error}"
    return STATUS_NO_SAMPLE, reason


def run_prefetch(candidates, *, event_scope, scope_refs=(), trade_date,
                 asset_tickers=DEFAULT_ASSET_TICKERS, window_type=DEFAULT_WINDOW_TYPE,
                 conn=None, predict_fn=None, max_candidates=MAX_PREFETCH_CANDIDATES,
                 source_label="预取") -> dict:
    """候选 → 时点过滤/去重/截断 → 逐条 `predict_impact` → 结果字典。

    返回值（Prompt 变量 + 结构化事件组装输入）：
        {event_scope, scope_refs, trade_date, asset_tickers, window_type,
         candidates, candidate_count, filter_stats, status, reason}
    candidates 每条 = 候选字段 + {history_match_status, historical_impact, reason}。

    `scope_refs` 经 `normalize_route_refs` 归一 + 统一截断（`MAX_ROUTE_REFS`，
    评审 M10），截断/丢弃事实记入 `filter_stats["route_refs"]`（覆盖统计）。

    失败降级：单条异常/无条件 → `api_unavailable`；无样本 → `no_sample`；
    覆盖不足（资产未登记）→ `coverage_missing`（评审 m16）；部分候选有统计 →
    整体 `partial`。任何单条失败都不得中断本轮预取；单条异常先 `conn.rollback()`
    防事务 aborted 连坐后续查询（评审 M4）。
    """
    scope = _normalize_scope(event_scope)
    # 目标引用归一 + 统一截断（评审 M10）：候选侧路由构造与预测侧 `scope_refs`
    # 共用同一结果（对已归一/已截断输入幂等）
    refs, refs_meta = normalize_route_refs(scope_refs)
    base = {
        "event_scope": scope,
        "scope_refs": refs,
        "trade_date": str(trade_date),
        "asset_tickers": list(asset_tickers),
        "window_type": window_type,
        "candidates": [],
        "candidate_count": 0,
        "filter_stats": {"route_refs": refs_meta},
        "status": STATUS_EMPTY,
        "reason": None,
    }

    # 下层路由无命中目标 → 空结果（不跨层借用沪深 300 CAR）
    if scope in (SCOPE_SECTOR, SCOPE_STOCK) and not refs:
        return dict(base, reason="无命中目标（作用域路由为空，不跨层借用沪深 300 CAR）")

    selected, filter_stats = select_candidates(candidates, trade_date, max_candidates)
    filter_stats["route_refs"] = refs_meta
    base["filter_stats"] = filter_stats
    if not selected:
        reason_parts = []
        if filter_stats.get("future"):
            reason_parts.append(f"{filter_stats['future']} 条晚于交易日被排除")
        if filter_stats.get("undated"):
            reason_parts.append(f"{filter_stats['undated']} 条无可用时间被排除")
        if filter_stats.get("unparsed"):
            # 交易日不可解析 → 可见性不可判定，保守排除但必须显式计入覆盖统计
            refs_text = "、".join(filter_stats.get("unparsed_refs") or [])
            reason_parts.append(
                f"{filter_stats['unparsed']} 条因交易日不可解析无法判定可见性被排除"
                + (f"（原始时间: {refs_text}）" if refs_text else "")
            )
        reason = "；".join(reason_parts) or "无候选事件"
        return dict(base, reason=reason)

    need_default_predictor = predict_fn is None
    if need_default_predictor and conn is None:
        conn = open_conn()
        if conn is None:
            return dict(base, candidate_count=len(selected), status=STATUS_API_UNAVAILABLE,
                        reason="事件研究库连接不可用")
    predictor = predict_fn or _default_predict_fn()
    if predictor is None:
        return dict(base, candidate_count=len(selected), status=STATUS_API_UNAVAILABLE,
                    reason="事件研究预测器不可用")

    asset = asset_tickers[0] if asset_tickers else None
    entries = []
    first_error = None
    for cand in selected:
        entry = dict(cand)
        try:
            result = predictor(
                conn, cand.get("title") or cand.get("summary") or "",
                asset, window_type=window_type,
                event_type=cand.get("event_type"),
                event_subtype=cand.get("event_subtype"),
                event_condition=cand.get("event_condition"),
                as_of=cand.get("event_time"),
                event_scope=scope,
                scope_refs=tuple(refs),
                # 排除候选事件自身（评审 m14）：候选来自事件库（已有 event_impacts，
                # 含同资产/窗口行的可能），不排除会把自身已实现影响算作历史样本
                exclude_event_id=cand.get("event_id"),
            )
            status, impact, reason = _history_from_result(result, asset, window_type)
        except Exception as e:  # 单条失败不阻塞（不得由 LLM 补写统计）
            # 本地 PG 连接为 autocommit=False：失败语句会把事务置为 aborted，
            # 不回滚则后续所有语句都会以 "current transaction is aborted"
            # 失败并被吞成 api_unavailable（评审 M4）
            safe_rollback(conn)
            logger.warning(f"[{source_label}] 事件研究查询失败: {e}")
            status, impact, reason = STATUS_API_UNAVAILABLE, None, f"事件研究查询失败: {e}"
            if first_error is None:
                first_error = reason
        entry["history_match_status"] = status
        entry["historical_impact"] = impact
        entry["reason"] = reason
        entries.append(entry)

    status, reason = _aggregate_status(entries, first_error=first_error)
    return dict(base, candidates=entries, candidate_count=len(entries),
                status=status, reason=reason)


def degraded_result(*, event_scope, trade_date, reason,
                    asset_tickers=DEFAULT_ASSET_TICKERS,
                    window_type=DEFAULT_WINDOW_TYPE) -> dict:
    """预取不可用占位结果（节点级异常兜底）。

    与 `run_prefetch` 返回结构同形，供 Prompt 数据块渲染与结构化事件组装复用；
    `historical_impact` 恒为空（不得由 LLM 补写统计）。
    """
    return {
        "event_scope": _normalize_scope(event_scope),
        "scope_refs": [],
        "trade_date": str(trade_date),
        "asset_tickers": list(asset_tickers),
        "window_type": window_type,
        "candidates": [],
        "candidate_count": 0,
        "filter_stats": {},
        "status": STATUS_API_UNAVAILABLE,
        "reason": str(reason),
    }


# ==================== Prompt 数据块渲染 ====================

_DIRECTION_TEXT = {1: "利好", -1: "利空", 0: "中性"}


def _pct(value, digits=2):
    if value is None:
        return "无"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "无"


def _direction_text(value):
    if value is None:
        return "未知"
    return _DIRECTION_TEXT.get(int(value), f"未知({value})")


def render_prefetch_block(result, heading="事件研究历史统计（预取）") -> str:
    """预取结果 → Prompt 数据块（Markdown）。

    只渲染结构化字段：标题/时间/来源取自候选，统计全部取自预测器返回值
    （`historical_impact`），**不含候选摘要正文**——历史统计块不得引用新闻
    文本冒充 CAR 证据（方案第三章「板块/个股事件生产与消费」）。
    """
    result = result or {}
    refs = result.get("scope_refs") or []
    assets = result.get("asset_tickers") or []
    lines = [
        f"# {heading}",
        f"- 作用域: {result.get('event_scope') or '-'}"
        f"（目标: {'、'.join(refs) if refs else '无（市场层）'}）"
        f" | 窗口: {result.get('window_type') or '-'}"
        f" | 资产: {'、'.join(assets) if assets else '-'}"
        f" | 截止交易日: {result.get('trade_date') or '-'}",
    ]
    candidates = result.get("candidates") or []
    with_stats = [c for c in candidates if c.get("historical_impact")]
    status_line = f"- 预取状态: {result.get('status') or STATUS_EMPTY}"
    if result.get("reason"):
        status_line += f"（原因: {result['reason']}）"
    status_line += f"（候选 {len(candidates)} 条，统计可用 {len(with_stats)} 条）"
    lines.append(status_line)
    # 交易日不可解析 → 可见性不可判定，候选被保守排除：覆盖统计必须显式透出
    # （评审 m15 残留），否则预取"静默为空"，Prompt 无法区分「无事件」与「时点不可用」
    filter_stats = result.get("filter_stats") or {}
    unparsed = filter_stats.get("unparsed") or 0
    if unparsed:
        unparsed_refs = "、".join(filter_stats.get("unparsed_refs") or [])
        lines.append(
            f"- 覆盖统计: {unparsed} 条候选因交易日不可解析无法判定可见性被排除"
            + (f"（原始时间: {unparsed_refs}）" if unparsed_refs else "")
        )
    lines.append(
        "> 统计仅来自事件研究系统预取结果（已审核样本，as_of=事件时间防前视）；"
        "无样本或查询不可用时只标注原因，不得由模型补写数值，也不得引用新闻文本冒充 CAR 证据。"
    )
    for index, cand in enumerate(candidates, 1):
        time_text = cand.get("event_time") or "时间未知"
        source = cand.get("source") or "未知来源"
        lines.append("")
        lines.append(f"## {index}. 《{cand.get('title') or '（无标题）'}》 {time_text}（{source}）")
        impact = cand.get("historical_impact")
        if not impact:
            lines.append(f"- 匹配状态: {cand.get('history_match_status') or STATUS_NO_SAMPLE}"
                         f" | 原因: {cand.get('reason') or _REASON_NO_SAMPLE}")
            continue
        lines.append(
            f"- 匹配状态: {cand.get('history_match_status')}"
            f" | 样本数: {impact.get('sample_count')}"
            f"（模板 {impact.get('template_sample_count')}"
            f" / 相似 {impact.get('supplement_sample_count')}）"
            f" | 污染样本: {impact.get('contaminated_sample_count')}"
        )
        lines.append(
            f"- 加权 CAR: {_pct(impact.get('weighted_car'))}"
            f" | 模板平均 CAR: {_pct(impact.get('avg_car'))}"
            f" | 胜率: {_pct(impact.get('win_rate'), 1)}"
            f" | 方向: {_direction_text(impact.get('predicted_direction'))}"
            f" | 置信度: {impact.get('confidence') if impact.get('confidence') is not None else '无'}"
        )
        if impact.get("note"):
            lines.append(f"- 备注: {impact['note']}")
    return "\n".join(lines)


# ==================== 结构化事件组装（三字段同构） ====================

def candidate_to_event(candidate, *, event_scope, trade_date, scope_refs=(), fact=None,
                       confidence=None, market_confirmation=None, history_match_status=None,
                       historical_impact=None, history_reason=None, extra=None) -> dict:
    """候选 → 结构化事件（方案第三章三字段同构契约 + 第五章市场层扩展）。

    缺省引用候选自带的预测结果（`history_match_status`/`historical_impact`）；
    未匹配到预取候选时传 `history_match_status=STATUS_UNMATCHED` 且
    `historical_impact=None` + 原因。**统计只引用预取结果，不做任何推算。**
    """
    candidate = candidate or {}
    impact = historical_impact if historical_impact is not None else candidate.get("historical_impact")
    status = history_match_status or candidate.get("history_match_status") or STATUS_UNMATCHED
    reason = history_reason if history_reason is not None else candidate.get("reason")
    if impact is None and not reason:
        reason = _REASON_NO_SAMPLE
    notes = []
    if reason:
        notes.append(reason)
    if candidate.get("time_precision") == PRECISION_DATE:
        notes.append("事件时间仅日期精度（同日样本已保守排除）")
    if candidate.get("time_precision") == PRECISION_NONE:
        notes.append("事件时间不可解析")
    event = {
        "event_id": candidate.get("candidate_id") or candidate_id(
            candidate.get("source") or "", candidate.get("title") or ""),
        "event_scope": event_scope,
        "affected_scope_refs": list(scope_refs or []),
        "event_type": candidate.get("event_type"),
        "event_subtype": candidate.get("event_subtype"),
        "event_condition": candidate.get("event_condition"),
        "event_time": candidate.get("event_time"),
        "fact": (fact if fact is not None else candidate.get("title")) or "",
        "market_confirmation": market_confirmation,
        "history_match_status": status,
        "historical_impact": impact,
        "confidence": confidence,
        "source": candidate.get("source"),
        "source_url": candidate.get("source_url"),
        "data_quality": {
            "as_of": str(trade_date),
            "time_precision": candidate.get("time_precision"),
            "history_basis": "event_study_prefetch",
            "notes": notes,
        },
    }
    if extra:
        event.update(extra)
    return event


def candidates_to_events(candidates, *, event_scope, trade_date, scope_refs=(), confidence=None) -> list[dict]:
    """候选列表 → 结构化事件列表（板块/个股层：无 LLM 解析步骤，直接引用预取结果）。

    confidence 缺省取预测器置信度（`historical_impact.confidence`），无统计时为 None
    （统计口径只来自预取，不臆造）。
    """
    events = []
    for cand in candidates or []:
        value = confidence
        if value is None:
            value = (cand.get("historical_impact") or {}).get("confidence")
        events.append(candidate_to_event(
            cand, event_scope=event_scope, trade_date=trade_date, scope_refs=scope_refs,
            confidence=value,
        ))
    return events


# ==================== 同名匹配（市场层结构化输出用） ====================

def _bigrams(text: str) -> set:
    return {text[i:i + 2] for i in range(len(text) - 1)} or ({text} if text else set())


def title_similarity(left, right) -> float:
    """标题相似度（字符二元组 Jaccard，0-1）。"""
    a, b = _bigrams(normalize_title(left)), _bigrams(normalize_title(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_candidate(title, candidates, *, threshold=0.5, used=None):
    """标题 → 最相似的未使用候选（低于 threshold 返回 None）。"""
    best, best_score = None, threshold
    for cand in candidates or []:
        key = cand.get("candidate_id") or id(cand)
        if used is not None and key in used:
            continue
        score = title_similarity(title, cand.get("title") or cand.get("summary") or "")
        if score >= best_score:
            best, best_score = cand, score
    if best is None:
        return None
    if used is not None:
        used.add(best.get("candidate_id") or id(best))
    return best

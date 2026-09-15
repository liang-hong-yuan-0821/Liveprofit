"""
审核模块数据访问层（方案 3.2）

- 待审核草稿存 Redis（events:pending:{draft_id}），审核通过/忽略后写入 PG
- 影响结果草稿存 Redis（event_impacts:draft:{event_id}），人工勾选后落表 PG
- 审核日志写入 Redis 全局列表 event_review_log（LPUSH JSON，C2）
- 作用域/目标引用（event_scope / affected_scope_refs，三级路由）：归一化、
  严格校验（格式/存在性/组合）与按路由 + 时点读取，见本模块「作用域与目标引用」节
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from AI.eventStudy.collectors.config import (
    KEY_PENDING_EVENT, KEY_REVIEW_LOG, get_redis_client, is_redis_available,
)
from AI.eventStudy.processing import impact_writer

logger = logging.getLogger(__name__)


# ==================== 作用域与目标引用（三级事件路由） ====================
#
# 数据模型（方案第三章）：
#   event_scope          market / sector / stock，最细作用层级，单事件只属一层
#   affected_scope_refs  层内目标引用数组（market 固定 []），规范形态：
#                          SW:<6 位申万一级代码>        如 SW:801080
#                          CONCEPT:<东财 dc 代码>       如 CONCEPT:BK1753.DC
#                          stock:<6 位代码.SH|SZ|BJ>    如 stock:600519.SH
# 写入即归一化为上述规范形态（大小写/前缀/后缀统一），保证 GIN 包含查询精确命中。
# 校验分两层：格式与组合（本模块 normalize/resolve）+ 存在性（market schema 表
# market.stock_info / market.sector(source='dc') + 行业表 market.industry）。

SCOPE_MARKET = "market"
SCOPE_SECTOR = "sector"
SCOPE_STOCK = "stock"
SCOPE_VALUES = (SCOPE_MARKET, SCOPE_SECTOR, SCOPE_STOCK)

MAX_SCOPE_REFS = 20  # 单事件目标引用上限（bounds 存在性校验查询次数）

_SW_PREFIX = "SW:"
_CONCEPT_PREFIX = "CONCEPT:"
_STOCK_PREFIX = "stock:"

# 申万一级行业代码（6 位，容忍 Tushare index_classify 的 .SI 后缀）
_SW_CODE_RE = re.compile(r"^\d{6}$")
# 裸代码推断行业用：申万一级代码虽为 6 位，但全部以 80 开头——
# 裸 6 位码仅 80 开头可无歧义判为行业；其余裸码（如 600519）判定为非法，
# 强制写全交易所后缀（600519.SH），不猜测交易所
_SW_BARE_RE = re.compile(r"^80\d{4}$")
# 统一市场后缀股票代码（沪 .SH / 深 .SZ / 北 .BJ）
_TS_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
# 东财 dc 概念代码（source 锁定 dc，如 BK1753.DC；同花顺 .TI 不在本体系内）
_DC_CONCEPT_RE = re.compile(r"^BK\d{4}\.DC$")

_REF_SPLIT_RE = re.compile(r"[,，;；\n]+")


class EventScopeValidationError(Exception):
    """作用域/目标引用校验失败（行级失败语义，**非**"草稿不存在"）。

    与 approve_event 的 ValueError（草稿不存在/已过期）严格区分：平台侧
    审核服务据此映射 REVIEW_ROW_FAILED，不得映射 REVIEW_DRAFT_NOT_FOUND。
    """


@dataclass(frozen=True)
class EventRoute:
    """历史样本路由（查询侧）：作用域 + 层内目标引用。"""

    scope: str
    scope_refs: tuple[str, ...] = ()


def normalize_scope(value) -> str | None:
    """作用域归一：收敛三值（大小写/空白容错）；非法或缺失返回 None。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in SCOPE_VALUES else None


def normalize_scope_ref(value) -> str | None:
    """单条目标引用归一为规范形态；无法识别返回 None。

    接受规范形态与裸代码（前端/预取器便利）：801080/801080.SI → SW:801080、
    BK1753.DC → CONCEPT:BK1753.DC、600519.SH → stock:600519.SH。
    显式前缀与代码形态不匹配时返回 None（如 stock:801080）；裸 6 位码仅
    80 开头判为行业（申万一级代码特征），裸 600519 这类无法判定交易所的
    返回 None（不做跨类型/跨市场猜测，交由校验层报"格式非法"）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if upper.startswith(_SW_PREFIX):
        body = text[len(_SW_PREFIX):].strip().upper()
        if body.endswith(".SI"):
            body = body[:-3]
        return f"{_SW_PREFIX}{body}" if _SW_CODE_RE.match(body) else None
    if upper.startswith(_CONCEPT_PREFIX):
        body = text[len(_CONCEPT_PREFIX):].strip().upper()
        return f"{_CONCEPT_PREFIX}{body}" if _DC_CONCEPT_RE.match(body) else None
    if upper.startswith(_STOCK_PREFIX.upper()):  # 规范形态为小写 stock:，比较用大写化后的串
        body = text[len(_STOCK_PREFIX):].strip().upper()
        return f"{_STOCK_PREFIX}{body}" if _TS_CODE_RE.match(body) else None
    code = text.upper()
    if code.endswith(".SI"):
        code = code[:-3]  # 裸代码同样容忍 Tushare index_classify 的申万后缀
    if _TS_CODE_RE.match(code):
        return f"{_STOCK_PREFIX}{code}"
    if _SW_BARE_RE.match(code):
        return f"{_SW_PREFIX}{code}"
    if _DC_CONCEPT_RE.match(code):
        return f"{_CONCEPT_PREFIX}{code}"
    return None


def _raw_ref_items(value) -> list:
    """展开引用输入为原始项列表（接受 list/tuple/set/逗号或换行分隔字符串/单值）。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = _REF_SPLIT_RE.split(value)
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    # None 与空白同样跳过（评审残留）：None 经 str() 成 "None" 会被判为未识别
    # 引用，把空占位项误伤成「格式非法」而整行拦截
    return [i for i in items if i is not None and str(i).strip()]


def normalize_scope_refs(value) -> list[str]:
    """引用列表归一：去重保序，丢弃无法识别的项（严格校验另经 resolve 拦截）。"""
    refs: list[str] = []
    for item in _raw_ref_items(value):
        ref = normalize_scope_ref(item)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


@dataclass(frozen=True)
class RouteExistence:
    """目标引用存在性校验数据源（事件研究同库 liveprofit public schema）。

    逐引用主键命中查询（引用条数上界 MAX_SCOPE_REFS）：
      stock:<ts_code>  → market.stock_info（含退市股，存在即认可）
      CONCEPT:<code>   → market.sector（source 锁定东财 dc）
      SW:<code>        → 行业表 market.industry（source='SW2021'）

    查询异常（连接不可用/码表未迁移）向上抛出：approve 路径拦行级失败，
    预填路径经 filter_existing_refs 转为 fail-open。
    """

    conn: object

    def ref_exists(self, ref: str) -> bool:
        if ref.startswith(_STOCK_PREFIX):
            sql, param = "SELECT 1 FROM market.stock_info WHERE ts_code = %s", ref[len(_STOCK_PREFIX):]
        elif ref.startswith(_CONCEPT_PREFIX):
            sql, param = (
                "SELECT 1 FROM market.sector WHERE source = 'dc' AND sector_code = %s",
                ref[len(_CONCEPT_PREFIX):],
            )
        elif ref.startswith(_SW_PREFIX):
            sql, param = ("SELECT 1 FROM market.industry WHERE source = 'SW2021' "
              "AND industry_code = %s"), ref[len(_SW_PREFIX):]
        else:
            return False
        return self.conn.execute(sql, (param,)).fetchone() is not None

    def missing_refs(self, refs) -> list[str]:
        """返回不存在的引用列表（保序）。"""
        return [ref for ref in refs if not self.ref_exists(ref)]


def filter_existing_refs(refs, existence: RouteExistence) -> list[str]:
    """存在性初筛（AI 预填用，fail-open）：数据源不可用时保留全部格式合法引用。

    服务端 approve 的严格校验（resolve_scope_fields）为最终防线。
    """
    if existence is None:
        return list(refs)
    try:
        missing = set(existence.missing_refs(refs))
    except Exception as e:
        logger.warning(f"引用存在性初筛不可用（保留全部格式合法引用）: {e}")
        return list(refs)
    return [ref for ref in refs if ref not in missing]


def normalize_scope_fields(review: dict) -> tuple[str, list[str]]:
    """非严格归一（ignore 路径 / 表单回退）：scope 非法或缺失 → market；market 固定 []。

    不校验存在性与组合——ignored 事件保存默认值但不参与检索。
    """
    scope = normalize_scope(review.get("event_scope")) or SCOPE_MARKET
    if scope == SCOPE_MARKET:
        return SCOPE_MARKET, []
    return scope, normalize_scope_refs(review.get("affected_scope_refs"))


def resolve_scope_fields(review: dict, existence: RouteExistence | None = None) -> tuple[str, list[str]]:
    """严格校验并归一（approve 路径）：格式 + 作用域—目标组合 + 存在性。

    - scope 缺失 → 回退 market（AI 预填不可用/解析失败时表单默认 market）
    - 格式非法 / 未识别引用 / 越层引用 / 缺目标 / 引用不存在 → EventScopeValidationError
    - existence 为 None（如无连接）时跳过存在性校验，仅做格式与组合校验
    """
    raw_scope = review.get("event_scope")
    scope = normalize_scope(raw_scope)
    if scope is None:
        if raw_scope is not None and str(raw_scope).strip():
            raise EventScopeValidationError(
                f"作用域非法: {raw_scope!r}（可选 {'/'.join(SCOPE_VALUES)}）"
            )
        scope = SCOPE_MARKET

    raw_items = _raw_ref_items(review.get("affected_scope_refs"))
    # 「格式非法」的**唯一**判据是存在无法识别的引用项（评审 M6）：不得用
    # 「去重后条数变少」反推格式非法——合法重复引用（同一目标写两次）经
    # `normalize_scope_refs` 去重后条数同样变少，会误伤整行。
    unrecognized = [
        str(i).strip() for i in raw_items
        if normalize_scope_ref(i) is None
    ]
    if unrecognized:
        raise EventScopeValidationError(
            "目标引用格式非法: " + ", ".join(unrecognized)
            + "（行业 SW:<6 位代码> / 概念 CONCEPT:<东财 dc 代码> / 个股 stock:<代码.SH|SZ|BJ>）"
        )
    refs = normalize_scope_refs(raw_items)  # 合法重复引用静默去重（保序）
    if len(refs) > MAX_SCOPE_REFS:
        raise EventScopeValidationError(f"目标引用数量超出上限 {MAX_SCOPE_REFS}: {len(refs)}")

    if scope == SCOPE_MARKET:
        if refs:
            raise EventScopeValidationError(f"market 作用域不允许目标引用: {', '.join(refs)}")
        return SCOPE_MARKET, []

    if not refs:
        raise EventScopeValidationError(f"{scope} 作用域至少需要一个目标引用")
    for ref in refs:
        is_stock = ref.startswith(_STOCK_PREFIX)
        if scope == SCOPE_STOCK and not is_stock:
            raise EventScopeValidationError(f"stock 作用域仅支持个股引用: {ref}")
        if scope == SCOPE_SECTOR and is_stock:
            raise EventScopeValidationError(f"sector 作用域不支持个股引用: {ref}")

    if existence is not None:
        missing = existence.missing_refs(refs)
        if missing:
            raise EventScopeValidationError(f"目标引用不存在: {', '.join(missing)}")
    return scope, refs


def check_scope_fields(review: dict, conn=None) -> str | None:
    """平台侧提交前前置校验入口：合法返回 None，失败返回错误信息（异常不跨边界）。

    与 review_dao.approve_event 内的严格校验共用同一规则（双保险）。
    """
    try:
        resolve_scope_fields(review, RouteExistence(conn) if conn is not None else None)
    except EventScopeValidationError as e:
        return str(e)
    except Exception as e:  # 存在性数据源不可用（连接/码表缺失）→ 同样拦截，行级失败
        logger.warning(f"作用域前置校验失败（存在性数据源不可用）: {e}")
        return f"作用域校验失败（存在性数据源不可用）: {e}"
    return None


_DATE_ONLY_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")


def _as_of_param(as_of) -> str | None:
    """时点上界 → SQL 参数。返回 None = **不做时间过滤**（调用方跳过该条件）。

    - None / 空串 / 纯空白 → None（无时点约束，与 predictor `_as_of_sql_param`
      的 None 语义一致；不得退化成「当日 00:00」静默排除同日事件）
    - **日期形态**归一为当日 23:59:59.999999（评审 M5）：`date` 对象，以及
      「时分秒微秒全零」的 `datetime`（常见 `datetime.combine(d, time())`
      写法语义是日边界，等同于当日）
    - 带时刻的 datetime / ISO 串原样保留，不扩大到当日末

    `announced_at` 是带时刻的时间戳：直接绑定 'YYYY-MM-DD' 会被 PG 解析为当日
    00:00，把同日已公布的事件（如 09:30）整片排除，与 `is_visible_at` 对
    datetime 精度「同日可见」的口径不一致（同日事件被静默漏取）。
    """
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        if (as_of.hour, as_of.minute, as_of.second, as_of.microsecond) == (0, 0, 0, 0):
            return f"{as_of.date().isoformat()} 23:59:59.999999"
        return as_of.isoformat()
    if isinstance(as_of, date):
        return f"{as_of.isoformat()} 23:59:59.999999"
    text = str(as_of).strip()
    if not text:
        return None
    for fmt in _DATE_ONLY_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
        return parsed.strftime("%Y-%m-%d 23:59:59.999999")
    return text


def list_approved_events_for_route(conn, route: EventRoute, as_of, limit: int = 20) -> list[dict]:
    """按作用域 + 时点读取已审核事件（三级路由预取用，方案 3.2）。

    固定过滤 status='approved' AND announced_at <= as_of（防前视）。
    - market：event_scope = 'market' 或 NULL（历史行归一，只在市场层可见）
    - sector/stock：event_scope 精确匹配 + affected_scope_refs JSONB 数组命中查询
      （`?|`：route 多个引用为并集——事件命中任一目标即属该路由；单引用与包含
      查询语义等价。引用经同一归一化，可传裸代码；代码精确匹配不做前缀/模糊）
    - 空目标（sector/stock 无引用）不构成路由 → 返回 []，不落到全量事件
    - limit 为返回条数上界（默认 20），按 announced_at 降序（近事优先）
    - `as_of` 仅日期形态归一为当日末（23:59:59.999999，评审 M5）：同日
      公布（如 09:30）事件可见，与 `is_visible_at` 口径一致；
      `as_of` 为 None/空串/纯空白 → **跳过** `announced_at <= as_of` 条件
      （不做时间过滤，与 predictor 语义一致）

    引用无法识别或作用域非法时抛 ValueError（调用方编程错误，不静默降级）。
    目标引用的重复项静默去重（评审 M6；不因去重判非法）。
    """
    scope = normalize_scope(route.scope)
    if scope is None:
        raise ValueError(f"路由作用域非法: {route.scope!r}（可选 {'/'.join(SCOPE_VALUES)}）")
    limit = int(limit)
    if limit < 1:
        raise ValueError(f"limit 必须为正整数: {limit}")
    as_of = _as_of_param(as_of)

    sql = (
        "SELECT event_id, title, content, event_type, event_subtype, event_condition, "
        "       announced_at, trading_day, importance, event_scope, affected_scope_refs, source_url "
        "FROM events WHERE status = 'approved'"
    )
    params: list = []
    # 时点条件**按需拼接**：None = 不做时间过滤（与 predictor `_as_of_sql_param`
    # 一致），不得退化成绑定 None/空串（PG 侧类型/语义都不确定）
    if as_of is not None:
        sql += " AND announced_at <= %s::timestamptz"
        params.append(as_of)
    if scope == SCOPE_MARKET:
        sql += " AND (event_scope = %s OR event_scope IS NULL)"
        params.append(SCOPE_MARKET)
    else:
        raw_refs = route.scope_refs
        raw_items = _raw_ref_items(raw_refs)
        # 与 `resolve_scope_fields` 同口径（评审 M6）：只有「无法识别的引用项」
        # 判非法；合法重复引用去重后不视为格式问题
        unrecognized = [str(i).strip() for i in raw_items
                        if normalize_scope_ref(i) is None]
        if unrecognized:
            raise ValueError(f"路由目标引用格式非法: {', '.join(unrecognized)}（{raw_refs!r}）")
        refs = normalize_scope_refs(raw_items)
        if not refs:
            return []  # 无目标 = 无路由（sector/stock 不落到全量事件）
        # ?| = jsonb 数组「存在任一」：route 多目标为并集（命中任一即属本路由），
        # 单目标时与 @> 包含查询等价；GIN(jsonb_ops) 可加速该运算符。
        sql += " AND event_scope = %s AND affected_scope_refs ?| %s::text[]"
        params.extend([scope, refs])
    sql += " ORDER BY announced_at DESC, event_id DESC LIMIT %s"
    params.append(limit)

    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "event_id": int(row[0]),
            "title": row[1],
            "content": row[2],
            "event_type": row[3],
            "event_subtype": row[4],
            "event_condition": row[5],
            "announced_at": row[6].isoformat() if isinstance(row[6], datetime) else row[6],
            "trading_day": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
            "importance": row[8],
            "event_scope": normalize_scope(row[9]) or SCOPE_MARKET,  # 历史行 NULL → market
            "affected_scope_refs": normalize_scope_refs(row[10]),
            "source_url": row[11],
        }
        for row in rows
    ]


# ==================== 审核日志 ====================

def _log_review(event_ref, operator: str, action: str, changed_fields: dict = None):
    """LPUSH 审核动作 JSON 到 event_review_log（仅追溯保险，不建审计表）。"""
    if not is_redis_available():
        return
    entry = {
        "event_ref": str(event_ref),
        "operator": operator,
        "time": datetime.now().isoformat(),
        "action": action,
        "changed_fields": changed_fields or {},
    }
    try:
        get_redis_client().lpush(KEY_REVIEW_LOG, json.dumps(entry, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"审核日志写入失败: {e}")


# ==================== 待审核事件 ====================

def get_pending_events() -> list[dict]:
    """从 Redis 读取待审核事件草稿（3.2.1 接口）。"""
    if not is_redis_available():
        return []
    events = []
    for key in get_redis_client().scan_iter("events:pending:*", count=100):
        raw = get_redis_client().get(key)
        if raw is None:
            continue
        try:
            draft = json.loads(raw)
            draft["draft_id"] = int(key.split(":")[-1])
            events.append(draft)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    events.sort(key=lambda e: e.get("announced_at", ""), reverse=True)
    return events


def get_pending_event(draft_id: int):
    """读取单条待审核草稿。"""
    if not is_redis_available():
        return None
    raw = get_redis_client().get(KEY_PENDING_EVENT.format(draft_id=draft_id))
    if raw is None:
        return None
    try:
        draft = json.loads(raw)
        draft["draft_id"] = draft_id
        return draft
    except json.JSONDecodeError:
        return None


def _insert_event(conn, draft: dict, review: dict, status: str) -> int:
    """将草稿 + 审核字段写入 PG events 表，返回新 event_id。

    路由字段经 normalize_scope_fields 非严格归一（market + [] 默认链）；
    approve 路径已在 approve_event 内完成严格校验，此处不重复校验。
    """
    # 注意：数值 0 是合法值（如 CPI 环比 0.0），不能用 `or` 兜底
    actual = review.get("actual_value")
    if actual is None:
        actual = draft.get("actual_value")
    previous = review.get("previous_value")
    if previous is None:
        previous = draft.get("previous_value")
    scope, scope_refs = normalize_scope_fields(review)
    row = conn.execute(
        """
        INSERT INTO events (
            title, content, event_type, event_subtype, event_condition,
            announced_at, expected_value, actual_value, previous_value,
            importance, status, source_url, event_scope, affected_scope_refs
        )
        VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING event_id
        """,
        (
            draft.get("title", ""),
            draft.get("content") or "",
            review.get("event_type") or None,
            review.get("event_subtype") or None,
            review.get("event_condition") or None,
            draft.get("announced_at"),
            review.get("expected_value"),
            actual,
            previous,
            review.get("importance", draft.get("importance_hint") or 3),
            status,
            draft.get("source_url") or None,
            scope,
            json.dumps(scope_refs, ensure_ascii=False),
        ),
    ).fetchone()
    conn.commit()
    return int(row[0])


def _delete_draft(draft_id: int):
    if is_redis_available():
        get_redis_client().delete(KEY_PENDING_EVENT.format(draft_id=draft_id))


def approve_event(conn, draft_id: int, review_fields: dict, operator: str = "admin") -> int:
    """审核通过：校验作用域/目标 → 写入 PG（status='approved'）→ 删除草稿 → 记日志。

    校验失败抛 EventScopeValidationError（行级失败语义，草稿保留可修正重提），
    与"草稿不存在"的 ValueError 严格区分（平台侧映射 REVIEW_ROW_FAILED）。
    """
    draft = get_pending_event(draft_id)
    if draft is None:
        raise ValueError(f"待审草稿不存在或已过期: {draft_id}")
    resolve_scope_fields(review_fields, RouteExistence(conn))  # 校验（不落库）
    event_id = _insert_event(conn, draft, review_fields, "approved")
    _delete_draft(draft_id)
    _log_review(draft_id, operator, "approve", review_fields)
    logger.info(f"事件审核通过: draft={draft_id} → event_id={event_id}")
    return event_id


def ignore_event(conn, draft_id: int, operator: str = "admin") -> int:
    """忽略事件：写入 PG（status='ignored'，用于爬虫去重），删除草稿，记日志。"""
    draft = get_pending_event(draft_id)
    if draft is None:
        raise ValueError(f"待审草稿不存在或已过期: {draft_id}")
    event_id = _insert_event(conn, draft, {}, "ignored")
    _delete_draft(draft_id)
    _log_review(draft_id, operator, "ignore")
    logger.info(f"事件已忽略: draft={draft_id} → event_id={event_id}")
    return event_id


# ==================== 影响结果确认 ====================

def get_impact_drafts() -> list[dict]:
    """列出全部影响结果草稿（审核界面读取展示）。"""
    return impact_writer.list_impact_drafts()


def get_event_title(conn, event_id: int) -> str:
    row = conn.execute(
        "SELECT title FROM events WHERE event_id = %s", (event_id,)
    ).fetchone()
    return row[0] if row else f"事件 {event_id}"


def confirm_impacts(conn, event_id: int, selected_tickers: list,
                    operator: str = "admin") -> int:
    """人工勾选确认：仅将选中资产的窗口结果落表 PG event_impacts（B5）。

    Returns: 写入记录数。
    """
    draft = impact_writer.read_impact_draft(event_id)
    if draft is None:
        raise ValueError(f"影响草稿不存在或已过期: event_id={event_id}（可重算）")
    assets = draft.get("assets", {})
    count = 0
    records = []
    for ticker in selected_tickers:
        if ticker not in assets:
            logger.warning(f"草稿中无该资产结果: {ticker}")
            continue
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE ticker = %s", (ticker,)
        ).fetchone()
        if row is None:
            logger.warning(f"资产未初始化: {ticker}")
            continue
        asset_id = row[0]
        for wt, r in assets[ticker].items():
            if r.get("error"):
                continue  # 计算失败的窗口不落表
            records.append((
                event_id, asset_id, wt,
                r.get("window_days"),
                r.get("cumulative_abnormal_return"),
                r.get("t_stat"),
                r.get("direction", 0),
                bool(r.get("is_contaminated", False)),
            ))
    if records:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO event_impacts (
                    event_id, asset_id, window_type, window_days,
                    cumulative_abnormal_return, t_stat, direction, is_contaminated
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, asset_id, window_type) DO NOTHING
                """,
                records,
            )
        count = len(records)
    conn.commit()
    impact_writer.delete_impact_draft(event_id)
    _log_review(event_id, operator, "confirm_impacts", {"assets": selected_tickers})
    logger.info(f"影响结果确认落表: event={event_id}，{count} 条记录")
    return count

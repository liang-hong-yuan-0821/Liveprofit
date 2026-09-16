"""
名称→代码解析器（事件审核 AI 预填辅助，2026-09-16）

把 LLM 输出的中文实体名称（板块名/公司名）解析为规范引用代码
（stock:<ts_code> / SW:<code> / CONCEPT:<code>）。确定性查询，不调 LLM，
是「名称→代码」的唯一映射源——LLM 不凭记忆补代码（方案 4.1/4.2）。

字典（market schema，liveprofit 库，与 review_dao.RouteExistence 同库）：
- 个股：market.instrument（instrument_type='stock'，name → ts_code）
- 申万行业：market.industry（source='SW2021'，name → industry_code）
- 东财概念：market.sector（source='dc'，name → sector_code）

匹配规则（方案 4.1.1）：
1. **全局精确匹配优先**：先对全部字典表跑 name = ANY(names) 批量精确，
   全部名称试完后才对仍未解析的名称做包含兜底（层级内行业优先；同表
   多行命中按代码升序取首行，确定性）
2. 唯一包含兜底（批量）：仍未解析且长度 ≥2 的名称按 name ILIKE ANY(%s)
   批量查（参数绑定 ['%词%', ...]——psycopg3 下 SQL 文本直接写 %词% 会报
   占位符错误），**恰好命中 1 行**才采用；查询前对名称中的 LIKE 元字符
   %/_/\\ 转义
3. 行业与概念都命中（同一层级内）→ 行业优先（申万是互斥完备分类体系）
4. 作用域约束：scope='stock' 只查个股表；scope='sector' 先行业后概念
5. 失败语义：查询异常 → 每个 except 分支先 conn.rollback()（自身再包
   try）——共享 PG conn（autocommit=False），不回滚会事务中毒，同一批内
   后续全部语句（含逐草稿的 filter_existing_refs 初筛）静默失效；回滚后
   对应名称视为未解析（fail-open，不抛，不阻塞预填）

查询条数：stock 每批 2 条、sector 每批 4 条（industry/sector × 精确/包含），
不随名称数增长（方案 4.1.1 承诺）。
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 产出形态过滤（引用规范形态与 review_dao 一致）
_CN_TS_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
_DC_CONCEPT_RE = re.compile(r"^BK\d{4}\.DC$")
_SW_CODE_RE = re.compile(r"^\d{6}$")
# LIKE 元字符转义（配合 ILIKE '%词%' 参数绑定）
_LIKE_ESCAPE_RE = re.compile(r"([%_\\])")

MIN_CONTAINS_LEN = 2  # 唯一包含兜底的最小名称长度（单字不做包含匹配）


def _escape_like(name: str) -> str:
    return _LIKE_ESCAPE_RE.sub(r"\\\1", name)


@dataclass(frozen=True)
class _DictSpec:
    """一张字典表的查询规格：精确 SQL、包含 SQL、代码 → 引用格式化、代码形态过滤。"""

    exact_sql: str
    contains_sql: str
    format_ref: object  # callable(code) -> ref
    code_ok: object     # callable(code) -> bool


_STOCK_SPEC = _DictSpec(
    "SELECT ts_code, name FROM market.instrument "
    "WHERE instrument_type = 'stock' AND name = ANY(%s)",
    "SELECT ts_code, name FROM market.instrument "
    "WHERE instrument_type = 'stock' AND name ILIKE ANY(%s)",
    lambda code: f"stock:{code}",
    lambda code: bool(_CN_TS_RE.match(code)),
)
_SW_SPEC = _DictSpec(
    "SELECT industry_code, name FROM market.industry "
    "WHERE source = 'SW2021' AND name = ANY(%s)",
    "SELECT industry_code, name FROM market.industry "
    "WHERE source = 'SW2021' AND name ILIKE ANY(%s)",
    lambda code: f"SW:{code}",
    lambda code: bool(_SW_CODE_RE.match(code)),
)
_CONCEPT_SPEC = _DictSpec(
    "SELECT sector_code, name FROM market.sector "
    "WHERE source = 'dc' AND name = ANY(%s)",
    "SELECT sector_code, name FROM market.sector "
    "WHERE source = 'dc' AND name ILIKE ANY(%s)",
    lambda code: f"CONCEPT:{code}",
    lambda code: bool(_DC_CONCEPT_RE.match(code)),
)

# sector 作用域的命中层级（精确层级内行业优先；方案 4.1.1 规则 1/3）
_SECTOR_SPECS = (_SW_SPEC, _CONCEPT_SPEC)


@dataclass(frozen=True)
class NameResolver:
    """名称→规范引用解析器（按作用域查字典表）。"""

    conn: object

    def resolve_names(self, names: list[str], scope: str) -> tuple[list[str], list[str]]:
        """名称 → (解析出的规范引用列表, 未解析名称列表)，均保序。

        scope='stock' 只查个股表；scope='sector' 先行业后概念（同一层级内
        行业优先）。**全局精确优先**：全部字典表的精确匹配跑完后，才对仍未
        解析的名称做唯一包含兜底（方案 4.1.1 规则 1-2）。
        调用方传其他作用域属编程错误，抛 ValueError 不静默降级
        （与 review_dao.list_approved_events_for_route 同姿态）。
        """
        if scope not in ("stock", "sector"):
            raise ValueError(f"解析作用域非法: {scope!r}（可选 stock/sector）")
        if not names:
            return [], []
        specs = (_STOCK_SPEC,) if scope == "stock" else _SECTOR_SPECS
        # 精确 + 包含各批量一次（每张表 ≤2 条查询，与名称数无关）
        exact_maps = [self._exact_map(spec, names) for spec in specs]
        contains_maps = [self._unique_contains_map(spec, names) for spec in specs]
        resolved: list[str] = []
        unresolved: list[str] = []
        for name in names:
            ref = None
            # 第一层：全局精确匹配（层级内行业优先）
            for spec, exact_map in zip(specs, exact_maps):
                ref = self._pick_exact(exact_map, name, spec)
                if ref is not None:
                    break
            # 第二层：唯一包含兜底（仍未解析的名称）
            if ref is None:
                for spec, contains_map in zip(specs, contains_maps):
                    ref = contains_map.get(name)
                    if ref is not None:
                        break
            (resolved if ref is not None else unresolved).append(ref or name)
        return resolved, unresolved

    # ---- 查询与清洗 ----

    def _query(self, sql: str, params) -> list | None:
        """执行查询；异常 → rollback + fail-open（返回 None = 查询失败，与空结果区分）。"""
        try:
            return self.conn.execute(sql, params).fetchall()
        except Exception as e:
            self._rollback()
            logger.warning(f"名称解析查询失败（fail-open，对应名称视为未解析）: {e}")
            return None

    def _rollback(self):
        try:
            self.conn.rollback()
        except Exception:
            pass

    def _exact_map(self, spec: _DictSpec, names: list[str]) -> dict:
        """批量精确匹配：{名称: [合格代码升序列表]}；查询失败返回空表（fail-open）。"""
        rows = self._query(spec.exact_sql, (names,))
        if rows is None:
            return {}
        out: dict[str, list[str]] = {}
        for code, name in rows:
            if not spec.code_ok(code):
                continue
            out.setdefault(name, []).append(code)
        for codes in out.values():
            codes.sort()  # 同表多行命中按代码升序取首行（确定性）
        return out

    def _unique_contains_map(self, spec: _DictSpec, names: list[str]) -> dict:
        """批量唯一包含兜底：{名称: 引用}——ILIKE ANY 恰好命中 1 行且代码形态合格才采用。

        查询失败/多行/零行 → 该名称不进入映射（fail-open）。
        归属判定：模式 `%转义词%` 转义后元字符全为字面量，Python 侧
        `词.casefold() in 行名.casefold()` 与 ILIKE 语义对齐（大小写不敏感）；
        大小写仍不符时该行不归属 → 宁可落 unresolved 交人工，不误挂代码。
        名称去重（dict.fromkeys）：重复名称会让同一行被多次计数，破坏
        「恰好 1 行」的唯一性判定（CR N1）。
        """
        eligible = list(dict.fromkeys(
            n for n in names if len(n) >= MIN_CONTAINS_LEN))
        if not eligible:
            return {}
        patterns = [f"%{_escape_like(n)}%" for n in eligible]
        rows = self._query(spec.contains_sql, (patterns,))
        if rows is None:
            return {}
        hits: dict[str, list[str]] = {}
        for code, row_name in rows:
            folded = row_name.casefold()
            for n in eligible:
                if n.casefold() in folded:
                    hits.setdefault(n, []).append(code)
        out: dict[str, str] = {}
        for n, codes in hits.items():
            if len(codes) == 1 and spec.code_ok(codes[0]):
                out[n] = spec.format_ref(codes[0])
        return out

    @staticmethod
    def _pick_exact(exact_map: dict, name: str, spec: _DictSpec) -> str | None:
        codes = exact_map.get(name)
        return None if not codes else spec.format_ref(codes[0])

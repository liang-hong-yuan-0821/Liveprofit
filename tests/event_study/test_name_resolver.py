"""
名称→代码解析器测试（2026-09-16，方案 4.7.1）

FakeConn 按 SQL 中的表名分派：精确查询按 params[0] 名称集合过滤预置行，
ILIKE 查询按剥离 % 后的模式做包含匹配；可注入表级异常模拟查询失败
（断言 rollback 被调用，评审 M1）。全部用例无真实 PG 依赖。
"""

import pytest

from AI.eventStudy.review.name_resolver import NameResolver, _escape_like


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    """execute 按 SQL 中出现的表名分派到 table_rows；failures 中的表抛异常。

    返回 psycopg3 同形态的游标对象（execute(...).fetchall()）。
    """

    def __init__(self, table_rows=None, failures=None):
        self.table_rows = table_rows or {}
        self.failures = failures or set()
        self.executed = []     # (sql, params)
        self.rollback_calls = 0

    def execute(self, sql, params):
        self.executed.append((sql, params))
        for table in ("market.instrument", "market.industry", "market.sector"):
            if table in sql:
                if table in self.failures:
                    raise RuntimeError(f"{table} 查询失败")
                rows = self.table_rows.get(table, [])
                if "ILIKE" in sql:
                    # ILIKE ANY：params[0] 为模式列表；模式 %转义词% 等价于字面包含
                    # （大小写不敏感，与真实 ILIKE 对齐；转义反斜杠仅绑定参数断言用）
                    patterns = [p.strip("%") for p in params[0]]
                    return _FakeCursor([
                        (c, n) for (c, n) in rows
                        if any(pat.casefold() in n.casefold() for pat in patterns)
                    ])
                names = set(params[0])  # name = ANY(%s)
                return _FakeCursor([(c, n) for (c, n) in rows if n in names])
        return _FakeCursor([])

    def rollback(self):
        self.rollback_calls += 1


def quoted_tables(conn: FakeConn) -> set[str]:
    """执行过的 SQL 命中的表集合（作用域约束断言用）。"""
    tables = {"market.instrument", "market.industry", "market.sector"}
    return {t for sql, _ in conn.executed for t in tables if t in sql}


# ==================== 精确匹配 / 唯一包含 / 确定性 ====================

def test_stock_exact_match():
    conn = FakeConn(table_rows={"market.instrument": [("600519.SH", "贵州茅台")]})
    refs, unresolved = NameResolver(conn).resolve_names(["贵州茅台"], "stock")
    assert refs == ["stock:600519.SH"]
    assert unresolved == []


def test_sector_exact_match_industry_and_concept():
    conn = FakeConn(table_rows={
        "market.industry": [("801080", "电子")],
        "market.sector": [("BK1753.DC", "光刻胶")],
    })
    refs, unresolved = NameResolver(conn).resolve_names(["电子", "光刻胶"], "sector")
    assert refs == ["SW:801080", "CONCEPT:BK1753.DC"]
    assert unresolved == []


def test_unique_contains_fallback():
    """「茅台」→ 唯一包含命中「贵州茅台」。"""
    conn = FakeConn(table_rows={"market.instrument": [("600519.SH", "贵州茅台")]})
    refs, unresolved = NameResolver(conn).resolve_names(["茅台"], "stock")
    assert refs == ["stock:600519.SH"]
    assert unresolved == []
    assert any("ILIKE" in sql for sql, _ in conn.executed)


def test_contains_multi_hit_rejected():
    """「光伏」命中光伏概念/光伏设备多行 → 不采用。"""
    conn = FakeConn(table_rows={"market.sector": [
        ("BK0588.DC", "光伏概念"), ("BK1031.DC", "光伏设备"),
    ]})
    refs, unresolved = NameResolver(conn).resolve_names(["光伏"], "sector")
    assert refs == []
    assert unresolved == ["光伏"]


def test_exact_same_name_multi_row_picks_min_code():
    """精确匹配同表多行命中 → 代码升序取首行（跨境电商 BK1115/BK1547）。"""
    conn = FakeConn(table_rows={"market.sector": [
        ("BK1547.DC", "跨境电商"), ("BK1115.DC", "跨境电商"),
    ]})
    refs, _ = NameResolver(conn).resolve_names(["跨境电商"], "sector")
    assert refs == ["CONCEPT:BK1115.DC"]


def test_industry_priority_over_concept():
    """同一名称在行业与概念都精确命中 → 行业优先。"""
    conn = FakeConn(table_rows={
        "market.industry": [("801080", "电子")],
        "market.sector": [("BK1036.DC", "电子")],
    })
    refs, _ = NameResolver(conn).resolve_names(["电子"], "sector")
    assert refs == ["SW:801080"]


def test_exact_across_dicts_beats_contains():
    """全局精确优先（方案规则 1）：概念表精确命中优于行业表唯一包含。"""
    conn = FakeConn(table_rows={
        "market.industry": [("801030", "基础化工")],
        "market.sector": [("BK9999.DC", "化工")],
    })
    refs, _ = NameResolver(conn).resolve_names(["化工"], "sector")
    assert refs == ["CONCEPT:BK9999.DC"]


def test_contains_fallback_across_dicts_industry_first():
    """精确全不命中 → 唯一包含兜底：行业与概念都唯一包含命中时行业优先。"""
    conn = FakeConn(table_rows={
        "market.industry": [("801030", "基础化工")],
        "market.sector": [("BK9999.DC", "化工业")],
    })
    refs, _ = NameResolver(conn).resolve_names(["化工"], "sector")
    assert refs == ["SW:801030"]


def test_duplicate_names_do_not_break_unique_contains():
    """重复名称去重后计数（CR N1）：同一名称写两次不得破坏「恰好 1 行」判定。"""
    conn = FakeConn(table_rows={"market.sector": [("BK0001.DC", "abcdef")]})
    refs, unresolved = NameResolver(conn).resolve_names(["abc", "abc"], "sector")
    assert refs == ["CONCEPT:BK0001.DC", "CONCEPT:BK0001.DC"]
    assert unresolved == []


def test_contains_attribution_case_insensitive():
    """归属判定与 ILIKE 对齐：大小写不符的唯一命中仍采用（CR N2）。"""
    conn = FakeConn(table_rows={"market.sector": [("BK0002.DC", "ChatGPT概念")]})
    refs, unresolved = NameResolver(conn).resolve_names(["chatgpt"], "sector")
    assert refs == ["CONCEPT:BK0002.DC"]
    assert unresolved == []


# ==================== 作用域约束 ====================

def test_stock_scope_only_queries_instrument():
    conn = FakeConn(table_rows={"market.industry": [("801080", "电子")]})
    refs, unresolved = NameResolver(conn).resolve_names(["电子"], "stock")
    assert refs == []
    assert unresolved == ["电子"]
    assert quoted_tables(conn) == {"market.instrument"}


def test_sector_scope_does_not_query_instrument():
    conn = FakeConn(table_rows={"market.instrument": [("600519.SH", "贵州茅台")]})
    refs, unresolved = NameResolver(conn).resolve_names(["贵州茅台"], "sector")
    assert refs == []
    assert unresolved == ["贵州茅台"]
    assert "market.instrument" not in quoted_tables(conn)


# ==================== 异常 fail-open + rollback ====================

def test_query_failure_fail_open_with_rollback():
    """查询异常 → 名称未解析、不抛、conn.rollback() 被调用（共享 conn 防事务中毒）。"""
    conn = FakeConn(
        table_rows={"market.instrument": [("600519.SH", "贵州茅台")]},
        failures={"market.instrument"},
    )
    refs, unresolved = NameResolver(conn).resolve_names(["贵州茅台"], "stock")
    assert refs == []
    assert unresolved == ["贵州茅台"]
    assert conn.rollback_calls >= 1


def test_sector_partial_failure_keeps_industry_result():
    """行业表正常、概念表异常 → 行业命中仍解析，概念表名称未解析（fail-open）。"""
    conn = FakeConn(
        table_rows={"market.industry": [("801080", "电子")]},
        failures={"market.sector"},
    )
    refs, unresolved = NameResolver(conn).resolve_names(["电子", "光刻胶"], "sector")
    assert refs == ["SW:801080"]
    assert unresolved == ["光刻胶"]
    assert conn.rollback_calls >= 1


# ==================== 边界 ====================

def test_empty_names_no_queries():
    conn = FakeConn()
    assert NameResolver(conn).resolve_names([], "sector") == ([], [])
    assert conn.executed == []


def test_invalid_scope_raises():
    with pytest.raises(ValueError):
        NameResolver(FakeConn()).resolve_names(["半导体"], "market")


def test_single_char_name_skips_contains():
    conn = FakeConn(table_rows={"market.sector": [("BK1036.DC", "半导体")]})
    refs, unresolved = NameResolver(conn).resolve_names(["半"], "sector")
    assert refs == []
    assert unresolved == ["半"]
    assert not any("ILIKE" in sql for sql, _ in conn.executed)


def test_like_metacharacters_escaped_in_contains():
    """LIKE 元字符转义：% 在绑定参数中带反斜杠，不匹配任意串（ILIKE ANY 批量模式）。"""
    conn = FakeConn(table_rows={"market.sector": [("BK1036.DC", "半导体")]})
    NameResolver(conn).resolve_names(["50%上涨"], "sector")
    contains_params = [p for sql, p in conn.executed if "ILIKE" in sql]
    assert contains_params
    for (patterns,) in contains_params:
        assert any("50\\%上涨" in pattern for pattern in patterns)


def test_non_cn_stock_code_rejected():
    """非 A 股代码形态（NVDA.O）被形态过滤拒绝 → 未解析。"""
    conn = FakeConn(table_rows={"market.instrument": [("NVDA.O", "英伟达")]})
    refs, unresolved = NameResolver(conn).resolve_names(["英伟达"], "stock")
    assert refs == []
    assert unresolved == ["英伟达"]


def test_escape_like():
    assert _escape_like("50%上涨_") == "50\\%上涨\\_"

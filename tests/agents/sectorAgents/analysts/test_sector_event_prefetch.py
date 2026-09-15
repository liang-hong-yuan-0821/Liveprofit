"""
单元测试：板块层事件研究预取（T4；纯函数 + fake conn/预测器，不依赖真实 LLM / 网络）。

覆盖：无命中目标返回空列表（不跨层借用沪深 300 CAR）、扫描范围→引用解析、
作用域路由与 as_of、时点防前视/去重/最多 5 条同构、单条失败降级、
政策新闻流与历史统计**分块注入**（历史统计块不得引用政策新闻流文本冒充 CAR 证据）。
"""

import pytest

from AI.sectorAgents.analysts.sector_event_prefetch import (
    prefetch_sector_event_study, resolve_sector_refs, scan_names_from_text,
)
from AI.sectorAgents.analysts.sector_news_analyst import (
    build_sector_prompt_variables, sector_scan_refs,
)
from AI.utils.event_prefetch_core import (
    STATUS_EMPTY, STATUS_OK, STATUS_PARTIAL, candidates_to_events, render_prefetch_block,
)

TRADE_DATE = "2026-08-08"
POLICY_NEWS = (
    "# 产业政策新闻\n"
    "- [2026-08-07 21:00] 工信部发布电子产业支持政策\n"
    "- 工信部政策解读：CAR 高达 99%（无发布时间）\n"
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """按 SQL 关键字分发的 PG 连接替身（行业码表 / 概念码表）。"""

    def __init__(self, industry_rows=(), concept_rows=()):
        self.industry_rows = list(industry_rows)
        self.concept_rows = list(concept_rows)
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if "market.industry" in sql:
            return FakeCursor(self.industry_rows)
        if "market.sector" in sql:
            return FakeCursor(self.concept_rows)
        return FakeCursor([])


def impact_result(*, samples=8, avg_car=-0.0231, weighted_car=-0.0176,
                  win_rate=0.375, confidence=0.55, status=STATUS_OK):
    return {
        "prediction": {
            "window_type": "post_event_5d", "asset_ticker": "000300.SH",
            "predicted_direction": -1, "predicted_return": weighted_car,
            "confidence": confidence,
        },
        "template_stats": {
            "sample_count": samples, "avg_car": avg_car, "win_rate": win_rate,
        },
        "supplement_events": [],
        "note": "",
        "sample_metadata": {
            "as_of": None, "event_scope": "sector", "scope_refs": [],
            "sample_count": samples, "template_sample_count": samples,
            "supplement_sample_count": 0, "contaminated_sample_count": 0,
            "contaminated": False, "history_match_status": status,
            "unavailable_channels": [], "reason": None,
        },
    }


def fake_predictor(behaviour):
    calls = []

    def predict(conn, text, asset, **kwargs):
        calls.append({"text": text, "asset": asset, **kwargs})
        value = behaviour.get(text)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return {
                "prediction": {}, "template_stats": {}, "supplement_events": [],
                "note": "",
                "sample_metadata": {
                    "sample_count": 0, "template_sample_count": 0,
                    "supplement_sample_count": 0, "contaminated": False,
                    "history_match_status": "no_sample",
                    "reason": "事件库暂无该资产/作用域下的已审核样本",
                },
            }
        return value

    return predict, calls


def fake_route_lister(rows):
    calls = []

    def lister(conn, route, as_of, limit=20):
        calls.append({"route": route, "as_of": as_of, "limit": limit})
        return rows

    return lister, calls


ROUTE_ROWS = [
    {"event_id": 11, "title": "工信部发布电子产业支持政策",
     "content": "支持电子产业链", "event_type": "产业政策", "event_subtype": None,
     "event_condition": None, "announced_at": "2026-08-07T21:00:00", "trading_day": None,
     "importance": 5, "event_scope": "sector", "affected_scope_refs": ["SW:801080"],
     "source_url": "https://example.com/11"},
]


# ==================== 扫描范围 → 路由引用 ====================

def test_resolve_sector_refs_codes_names_and_cross_scope_noise():
    """规范引用直通；名称经码表解析；越层/解析不到的条目只记未解析，不进路由。"""
    conn = FakeConn(industry_rows=[("电子", "801080"), ("医药生物", "801150")],
                    concept_rows=[("PCB", "BK1753.DC")])
    refs, missing = resolve_sector_refs(
        ["SW:801080", "电子", "BK1753.DC", "PCB", "600519.SH", "不存在的板块"], conn)
    assert refs == ["SW:801080", "CONCEPT:BK1753.DC"]
    assert all(r.startswith(("SW:", "CONCEPT:")) for r in refs)   # 无个股引用越层
    assert missing == ["600519.SH", "不存在的板块"]


def test_code_table_lookup_failure_rolls_back():
    """码表解析失败先回滚（M4 残留）：失败语句中毒事务，不回滚会连坐后续查询。"""
    order = []

    class BrokenTableConn(FakeConn):
        def execute(self, sql, params=None):
            order.append("industry" if "market.industry" in sql else "sector")
            raise RuntimeError("码表缺失")

        def rollback(self):
            order.append("rollback")

    refs, missing = resolve_sector_refs(["电子", "PCB"], BrokenTableConn())
    assert refs == []
    assert missing == ["电子", "PCB"]          # 解析失败按未命中处理（不猜测引用）
    # 两个码表查询各自失败后都先回滚
    assert order == ["industry", "rollback", "sector", "rollback"]


def test_scan_names_from_text_skips_headers_and_metrics():
    text = (
        "# 行业涨跌排名\n"
        "| 排名 | 行业 | 涨跌幅 |\n"
        "| 1 | 电子 | 3.25% |\n"
        "| 2 | 医药生物 | -1.20% |\n"
        "| 3 | - | 2.10% |\n"
    )
    assert scan_names_from_text(text) == ["电子", "医药生物"]


def test_sector_scan_refs_from_shortlist_and_scan_texts():
    state = {"sector_shortlist_structured": ["PCB"]}
    refs = sector_scan_refs(state, "| 1 | 电子 | 3.25% |", "| 2 | 医药生物 | -1.20% |")
    assert refs == ["PCB", "电子", "医药生物"]


# ==================== 无命中目标（不跨层借用） ====================

def test_no_scan_refs_returns_empty_without_prediction():
    predict, calls = fake_predictor({})
    lister, lister_calls = fake_route_lister(ROUTE_ROWS)
    result = prefetch_sector_event_study(
        scan_refs=[], raw_news=POLICY_NEWS, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=lister,
    )
    assert result["status"] == STATUS_EMPTY
    assert result["scope_refs"] == []
    assert result["candidates"] == []
    assert "不跨层借用沪深 300 CAR" in result["reason"]
    assert calls == [] and lister_calls == []      # 未发起任何预测/路由查询


def test_unresolvable_scan_refs_return_empty():
    predict, calls = fake_predictor({})
    result = prefetch_sector_event_study(
        scan_refs=["不存在的板块"], raw_news=POLICY_NEWS, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    assert result["candidates"] == []
    assert result["unresolved_refs"] == ["不存在的板块"]
    assert calls == []


def test_resolver_failure_degrades_to_no_hit():
    order = []

    class RollbackConn(FakeConn):
        def rollback(self):
            order.append("rollback")

    def boom(items, conn):
        order.append("resolve")
        raise RuntimeError("码表不可用")

    predict, calls = fake_predictor({})
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], raw_news=POLICY_NEWS, trade_date=TRADE_DATE,
        conn=RollbackConn(), predict_fn=predict, resolve_refs_fn=boom,
    )
    assert result["candidates"] == [] and result["scope_refs"] == []
    assert result["unresolved_refs"] == ["SW:801080"]
    assert calls == []
    # 解析兜底同样先回滚（M4 残留）：解析中的失败语句中毒事务
    assert order == ["resolve", "rollback"]


# ==================== 路由查询与逐条预测（同构） ====================

def test_route_query_uses_sector_scope_and_trade_date():
    predict, calls = fake_predictor({"工信部发布电子产业支持政策": impact_result()})
    lister, lister_calls = fake_route_lister(ROUTE_ROWS)
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=lister,
    )
    assert lister_calls[0]["as_of"] == TRADE_DATE
    assert lister_calls[0]["route"].scope == "sector"
    assert lister_calls[0]["route"].scope_refs == ("SW:801080",)
    assert result["scope_refs"] == ["SW:801080"]
    assert result["candidates"][0]["source"] == "事件库"
    assert result["candidates"][0]["history_match_status"] == STATUS_OK
    assert calls[0]["event_scope"] == "sector"
    assert calls[0]["scope_refs"] == ("SW:801080",)
    assert calls[0]["as_of"] == "2026-08-07T21:00:00"
    # 路由候选带 event_id → 取统计时排除自身（评审 m14，避免自相关污染）
    assert calls[0]["exclude_event_id"] == 11


def test_time_gate_and_max_five_isomorphic():
    predict, calls = fake_predictor({})
    lines = "".join(f"- [2026-08-0{day} 10:00] 板块事件{day}\n" for day in range(1, 8))
    lines += "- [2026-08-09 09:00] 晚于交易日的事件\n"
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], raw_news=lines, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    assert result["candidate_count"] == 5
    assert [c["title"] for c in result["candidates"]] == [
        "板块事件7", "板块事件6", "板块事件5", "板块事件4", "板块事件3",
    ]
    assert result["filter_stats"]["future"] == 1
    assert len(calls) == 5


def test_single_failure_does_not_block_sector_prefetch():
    predict, calls = fake_predictor({
        "工信部发布电子产业支持政策": impact_result(),
        "数据不及预期的板块事件": RuntimeError("事件研究 API 超时"),
    })
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"],
        raw_news="- [2026-08-07 21:00] 工信部发布电子产业支持政策\n"
                 "- [2026-08-07 20:00] 数据不及预期的板块事件\n",
        trade_date=TRADE_DATE, conn=FakeConn(), predict_fn=predict,
    )
    assert result["status"] == STATUS_PARTIAL
    statuses = {c["title"]: c["history_match_status"] for c in result["candidates"]}
    assert statuses["工信部发布电子产业支持政策"] == STATUS_OK
    assert statuses["数据不及预期的板块事件"] == "api_unavailable"


# ==================== M10：路由引用统一截断（候选/预测同口径） ====================

def test_normalize_route_refs_reports_truncation_and_drops():
    """截断/丢弃计数在归一入口单点产出（评审 M10）。"""
    from AI.utils import event_prefetch_core as core

    raw = ["SW:801080", "garbage"] + [f"SW:80{n:04d}" for n in range(1000, 1042)]
    refs, meta = core.normalize_route_refs(raw)
    assert refs[0] == "SW:801080"
    assert refs == [r for r in raw if r != "garbage"][:core.MAX_ROUTE_REFS]
    assert meta == {"input": 44, "kept": core.MAX_ROUTE_REFS,
                    "truncated": 3, "dropped": 1}
    # limit=None → 不截断
    refs_all, meta_all = core.normalize_route_refs(raw, limit=None)
    assert len(refs_all) == 43 and meta_all["truncated"] == 0


def test_refs_truncation_shared_by_route_and_predictor():
    """超过上限的引用在**同一入口**截断：候选侧路由与预测侧统计目标集合一致（评审 M10）。

    修复前候选侧按未截断目标扫描事件库、预测侧按 `[:40]` 截断后取统计——
    两侧目标不同会污染历史统计（命中候选与所引统计口径不一致）。
    """
    from AI.utils import event_prefetch_core as core

    refs = [f"SW:80{n:04d}" for n in range(1000, 1046)]   # 46 > MAX_ROUTE_REFS(40)
    keep = refs[:core.MAX_ROUTE_REFS]
    lister, lister_calls = fake_route_lister(ROUTE_ROWS)
    predict, calls = fake_predictor({})
    result = prefetch_sector_event_study(
        scan_refs=refs, raw_news="- [2026-08-07 21:00] 板块事件\n",
        trade_date=TRADE_DATE, conn=FakeConn(), predict_fn=predict,
        route_lister=lister,
    )
    # 候选侧（EventRoute）与预测侧（scope_refs）都是同一截断结果
    assert lister_calls[0]["route"].scope_refs == tuple(keep)
    assert calls[0]["scope_refs"] == tuple(keep)
    assert result["scope_refs"] == keep
    # 截断事实计入覆盖统计，不静默
    assert result["filter_stats"]["route_refs"] == {
        "input": 46, "kept": core.MAX_ROUTE_REFS, "truncated": 6, "dropped": 0,
    }


# ==================== 分块注入（政策新闻流 vs 历史统计） ====================

def test_history_block_does_not_quote_policy_news_stats():
    """历史统计块只含预取统计；政策原文里的「CAR 99%」不得进块。"""
    predict, _ = fake_predictor({"工信部发布电子产业支持政策": impact_result()})
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], raw_news=POLICY_NEWS, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    block = render_prefetch_block(result, heading="板块事件研究历史统计（预取）")
    assert "样本数: 8" in block
    assert "-1.76%" in block                     # 加权 CAR 来自预测器返回值
    assert "37.5%" in block                      # 胜率
    assert "99%" not in block                    # 政策原文统计不冒充 CAR 证据
    assert "无发布时间" not in block              # 无时间行不进候选/统计块


def test_prompt_variables_inject_policy_and_prefetch_as_separate_blocks():
    predict, _ = fake_predictor({"工信部发布电子产业支持政策": impact_result()})
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], raw_news=POLICY_NEWS, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    block = render_prefetch_block(result, heading="板块事件研究历史统计（预取）")
    variables = build_sector_prompt_variables(
        current_date=TRADE_DATE, sector_market_context="ctx", industry_perf="ip",
        fund_flow="ff", concept_heat="ch", policy_news=POLICY_NEWS,
        event_prefetch_block=block,
    )
    # 两块独立注入：当期事实流原文保留，历史统计块独立
    assert variables["policy_news"] == POLICY_NEWS
    assert variables["sector_event_prefetch"] == block
    assert "99%" in variables["policy_news"]
    assert "99%" not in variables["sector_event_prefetch"]
    assert variables["policy_news"] != variables["sector_event_prefetch"]


# ==================== 结构化事件（sector 字段同构） ====================

def test_sector_events_isomorphic_fields():
    predict, _ = fake_predictor({"工信部发布电子产业支持政策": impact_result()})
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=fake_route_lister(ROUTE_ROWS)[0],
    )
    events = candidates_to_events(result["candidates"], event_scope="sector",
                                  trade_date=TRADE_DATE)
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == "event:11"
    assert event["event_scope"] == "sector"
    assert event["affected_scope_refs"] == []
    assert event["event_type"] == "产业政策"
    assert event["event_time"] == "2026-08-07T21:00:00"
    assert event["fact"] == "工信部发布电子产业支持政策"
    assert event["history_match_status"] == STATUS_OK
    assert event["historical_impact"]["weighted_car"] == pytest.approx(-0.0176)
    assert event["historical_impact"]["sample_count"] == 8
    assert event["confidence"] == 0.55
    assert event["source"] == "事件库"
    assert event["data_quality"]["history_basis"] == "event_study_prefetch"
    assert event["data_quality"]["as_of"] == TRADE_DATE


def test_sector_events_keep_null_impact_with_reason():
    predict, _ = fake_predictor({})
    result = prefetch_sector_event_study(
        scan_refs=["SW:801080"], raw_news=POLICY_NEWS, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    events = candidates_to_events(result["candidates"], event_scope="sector",
                                  trade_date=TRADE_DATE)
    assert events and all(e["historical_impact"] is None for e in events)
    assert all(e["data_quality"]["notes"] for e in events)   # 无统计必有原因

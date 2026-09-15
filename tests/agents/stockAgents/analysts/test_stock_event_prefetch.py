"""
单元测试：个股层事件研究预取（T4；纯函数 + fake conn/预测器，不依赖真实 LLM / 网络）。

覆盖：`company_of_interest` → stock 路由引用（无后缀代码补市场后缀）、
无命中目标返回空列表（不跨层借用沪深 300 CAR）、越层引用不进 stock 路由、
作用域路由与 as_of 防前视、单条失败降级、节点级降级结果、结构化事件与 Prompt 注入。
"""

import pytest

from AI.stockAgents.analysts.news_analyst import build_stock_prompt_variables
from AI.stockAgents.analysts.stock_event_prefetch import (
    prefetch_stock_event_study, resolve_stock_refs,
)
from AI.utils.event_prefetch_core import (
    STATUS_API_UNAVAILABLE, STATUS_EMPTY, STATUS_OK, STATUS_PARTIAL,
    candidates_to_events, degraded_result, render_prefetch_block,
)

TRADE_DATE = "2026-08-08"
NEWS_TEXT = (
    "# 个股新闻\n"
    "- [2026-08-07 18:00] 公司发布业绩预增公告\n"
    "- 小道消息：CAR 高达 99%（无发布时间）\n"
)

ROUTE_ROWS = [
    {"event_id": 20, "title": "公司发布业绩预增公告", "content": "预计净利润翻倍",
     "event_type": "业绩", "event_subtype": None, "event_condition": None,
     "announced_at": "2026-08-07T18:00:00", "trading_day": None, "importance": 4,
     "event_scope": "stock", "affected_scope_refs": ["stock:600519.SH"],
     "source_url": "https://example.com/20"},
]


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self):
        self.closed = False

    def execute(self, sql, params=None):
        return FakeCursor([])

    def close(self):
        self.closed = True


def impact_result(*, samples=6, avg_car=-0.0210, weighted_car=-0.0180,
                  win_rate=0.3333, confidence=0.5, status=STATUS_OK):
    return {
        "prediction": {
            "window_type": "post_event_5d", "asset_ticker": "000300.SH",
            "predicted_direction": 1, "predicted_return": weighted_car,
            "confidence": confidence,
        },
        "template_stats": {
            "sample_count": samples, "avg_car": avg_car, "win_rate": win_rate,
        },
        "supplement_events": [],
        "note": "",
        "sample_metadata": {
            "as_of": None, "event_scope": "stock", "scope_refs": [],
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


# ==================== company_of_interest → 路由引用 ====================

def test_resolve_stock_refs_suffix_bare_code_and_empty():
    assert resolve_stock_refs("600519.SH") == (["stock:600519.SH"], [])
    assert resolve_stock_refs("600519") == (["stock:600519.SH"], [])
    assert resolve_stock_refs("000001") == (["stock:000001.SZ"], [])
    assert resolve_stock_refs("") == ([], [])


def test_cross_scope_ref_not_routed_to_stock():
    """越层引用（板块）不进 stock 路由：只记未解析，不猜测。"""
    refs, missing = resolve_stock_refs("SW:801080")
    assert refs == []
    assert missing == ["SW:801080"]


# ==================== 无命中目标（不跨层借用） ====================

def test_blank_ticker_returns_empty_without_prediction():
    predict, calls = fake_predictor({})
    lister, lister_calls = fake_route_lister(ROUTE_ROWS)
    result = prefetch_stock_event_study(
        ticker="", news_text=NEWS_TEXT, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=lister,
    )
    assert result["status"] == STATUS_EMPTY
    assert result["scope_refs"] == [] and result["candidates"] == []
    assert "不跨层借用沪深 300 CAR" in result["reason"]
    assert calls == [] and lister_calls == []


def test_unresolvable_ticker_returns_empty():
    predict, calls = fake_predictor({})
    result = prefetch_stock_event_study(
        ticker="SW:801080", news_text=NEWS_TEXT, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    assert result["candidates"] == []
    assert result["unresolved_refs"] == ["SW:801080"]
    assert calls == []


def test_route_without_hit_returns_empty_candidates():
    predict, calls = fake_predictor({})
    lister, _ = fake_route_lister([])
    result = prefetch_stock_event_study(
        ticker="600519.SH", news_text="", trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=lister,
    )
    assert result["status"] == STATUS_EMPTY
    assert result["candidates"] == []


# ==================== 路由命中 company_of_interest ====================

def test_route_query_hits_company_of_interest():
    predict, calls = fake_predictor({"公司发布业绩预增公告": impact_result()})
    lister, lister_calls = fake_route_lister(ROUTE_ROWS)
    result = prefetch_stock_event_study(
        ticker="600519", trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=lister,
    )
    assert lister_calls[0]["as_of"] == TRADE_DATE
    assert lister_calls[0]["route"].scope == "stock"
    assert lister_calls[0]["route"].scope_refs == ("stock:600519.SH",)
    assert result["scope_refs"] == ["stock:600519.SH"]
    assert result["candidates"][0]["event_id"] == 20
    assert result["candidates"][0]["source"] == "事件库"
    assert calls[0]["event_scope"] == "stock"
    assert calls[0]["scope_refs"] == ("stock:600519.SH",)
    assert calls[0]["as_of"] == "2026-08-07T18:00:00"      # 防前视


def test_time_gate_excludes_future_and_undated():
    predict, calls = fake_predictor({})
    result = prefetch_stock_event_study(
        ticker="600519.SH",
        news_text="- [2026-08-09 09:00] 晚于交易日\n- 无时间传闻\n",
        trade_date=TRADE_DATE, conn=FakeConn(), predict_fn=predict,
    )
    assert result["filter_stats"]["future"] == 1
    assert result["filter_stats"]["undated"] == 1
    assert calls == []


# ==================== 失败降级 ====================

def test_single_failure_degrades_without_blocking():
    predict, _ = fake_predictor({
        "公司发布业绩预增公告": impact_result(),
        "股东减持公告": RuntimeError("事件研究 API 超时"),
    })
    result = prefetch_stock_event_study(
        ticker="600519.SH",
        news_text="- [2026-08-07 18:00] 公司发布业绩预增公告\n"
                  "- [2026-08-07 17:00] 股东减持公告\n",
        trade_date=TRADE_DATE, conn=FakeConn(), predict_fn=predict,
    )
    assert result["status"] == STATUS_PARTIAL
    statuses = {c["title"]: c["history_match_status"] for c in result["candidates"]}
    assert statuses["公司发布业绩预增公告"] == STATUS_OK
    assert statuses["股东减持公告"] == STATUS_API_UNAVAILABLE


def test_all_failures_return_api_unavailable():
    predict, _ = fake_predictor({"公司发布业绩预增公告": RuntimeError("boom")})
    result = prefetch_stock_event_study(
        ticker="600519.SH", news_text="- [2026-08-07 18:00] 公司发布业绩预增公告\n",
        trade_date=TRADE_DATE, conn=FakeConn(), predict_fn=predict,
    )
    assert result["status"] == STATUS_API_UNAVAILABLE
    assert result["candidates"][0]["historical_impact"] is None


def test_node_level_degraded_result_is_empty_and_attributed():
    result = degraded_result(event_scope="stock", trade_date=TRADE_DATE,
                             reason="预取异常: PG 不可用")
    assert result["event_scope"] == "stock"
    assert result["status"] == STATUS_API_UNAVAILABLE
    assert result["candidates"] == []
    assert "预取异常" in result["reason"]
    block = render_prefetch_block(result, heading="个股事件研究历史统计（预取）")
    assert "api_unavailable" in block
    assert "不得由模型补写数值" in block


# ==================== 结构化事件与 Prompt 注入 ====================

def test_stock_events_isomorphic_fields():
    predict, _ = fake_predictor({"公司发布业绩预增公告": impact_result()})
    lister, _ = fake_route_lister(ROUTE_ROWS)
    result = prefetch_stock_event_study(
        ticker="600519.SH", trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict, route_lister=lister,
    )
    events = candidates_to_events(result["candidates"], event_scope="stock",
                                  trade_date=TRADE_DATE)
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == "event:20"
    assert event["event_scope"] == "stock"
    assert event["affected_scope_refs"] == []
    assert event["event_type"] == "业绩"
    assert event["event_time"] == "2026-08-07T18:00:00"
    assert event["fact"] == "公司发布业绩预增公告"
    assert event["history_match_status"] == STATUS_OK
    assert event["historical_impact"]["weighted_car"] == pytest.approx(-0.0180)
    assert event["historical_impact"]["sample_count"] == 6
    assert event["confidence"] == 0.5
    assert event["data_quality"]["history_basis"] == "event_study_prefetch"
    assert event["data_quality"]["notes"] == []


def test_prompt_variables_keep_news_and_prefetch_separate():
    predict, _ = fake_predictor({
        "公司发布业绩预增公告": impact_result(),
    })
    result = prefetch_stock_event_study(
        ticker="600519.SH", news_text=NEWS_TEXT, trade_date=TRADE_DATE,
        conn=FakeConn(), predict_fn=predict,
    )
    block = render_prefetch_block(result, heading="个股事件研究历史统计（预取）")
    variables = build_stock_prompt_variables(
        current_date=TRADE_DATE, ticker="600519.SH", company_name="贵州茅台",
        market_name="中国A股", instrument_context="inst", news_data=NEWS_TEXT,
        event_prefetch_block=block,
    )
    assert variables["news_data"] == NEWS_TEXT
    assert variables["stock_event_prefetch"] == block
    assert "99%" in variables["news_data"]                   # 当期事实流原文保留
    assert "99%" not in variables["stock_event_prefetch"]    # 统计块不含输入外统计
    assert "样本数: 6" in variables["stock_event_prefetch"]

"""
单元测试：市场层结构化事件输出（T4；对组装/解析纯函数，不用真实 LLM）。

覆盖：报告解析（表格 / 摘要块兜底 / 无事件降级）、结构化事件字段
（`event_id`/`event_scope=market`/`affected_scope_refs=[]`/分类/`event_time`/事实/
`market_confirmation`/`history_match_status`/`historical_impact`/`confidence`/来源/
`data_quality`）、历史统计只引用预取结果（报告中的 CAR 数字不得进统计字段）、
未匹配/无样本时 `historical_impact=None` + 原因。
"""

import json

import pytest

from AI.marketAgents.analysts.international_event_extraction import (
    build_international_events, parse_identified_events,
)
from AI.marketAgents.analysts.international_event_prefetch import (
    prefetch_event_study_evidence,
)

TRADE_DATE = "2026-08-08"


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def execute(self, sql, params=None):
        return FakeCursor([])


def impact_result(*, samples=12, avg_car=-0.0123, weighted_car=-0.0089,
                  win_rate=0.3333, confidence=0.42):
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
            "as_of": None, "event_scope": "market", "scope_refs": [],
            "sample_count": samples, "template_sample_count": samples,
            "supplement_sample_count": 0, "contaminated_sample_count": 0,
            "contaminated": False, "history_match_status": "ok",
            "unavailable_channels": [], "reason": None,
        },
    }


def fake_predictor(behaviour):
    def predict(conn, text, asset, **kwargs):
        value = behaviour.get(text)
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

    return predict


def run_prefetch(news="", macro="", behaviour=None):
    return prefetch_event_study_evidence(
        raw_news=news, central_bank_calendar="", macro_indicators=macro,
        trade_date=TRADE_DATE, conn=FakeConn(),
        predict_fn=fake_predictor(behaviour or {}),
    )


NEWS = (
    "# 全球宏观财经快讯\n"
    "- [2026-08-07 21:00] 美联储加息 25bp\n"
    "- [2026-08-07 20:00] 央行宣布降准 0.5 个百分点\n"
)

REPORT = """# 国际事件提取报告

## 一、已识别的重大事件

| 事件 | 类型 | 判断 | 影响方向 | 置信度 |
| :--- | :--- | :--- | :--- | :--- |
| 美联储加息 25bp | 货币政策 | 全球流动性收紧（报告估 CAR -8.8%） | 利空 | 75% |
| 中东地缘冲突升级 | 地缘政治 | 避险情绪上升 | 利空 | 0.6 |

## 二、事件描述摘要

【事件描述摘要】
- 美联储加息 25bp
"""


# ==================== 报告解析（纯函数） ====================

def test_parse_events_table():
    events = parse_identified_events(REPORT)
    assert [e["title"] for e in events] == ["美联储加息 25bp", "中东地缘冲突升级"]
    assert events[0]["event_type"] == "货币政策"
    assert events[0]["judgement"].startswith("全球流动性收紧")
    assert events[0]["direction"] == "利空"
    assert events[0]["confidence"] == pytest.approx(0.75)
    assert events[1]["confidence"] == pytest.approx(0.6)


def test_parse_events_digest_fallback():
    report = "# 报告\n\n【事件描述摘要】\n- 美联储加息 25bp\n- 央行宣布降准 0.5 个百分点\n"
    events = parse_identified_events(report)
    assert [e["title"] for e in events] == ["美联储加息 25bp", "央行宣布降准 0.5 个百分点"]
    assert all(e["confidence"] is None for e in events)


def test_parse_events_no_events_returns_empty():
    assert parse_identified_events("# 报告\n\n本轮无重大事件。\n") == []
    assert parse_identified_events("") == []


def test_headerless_table_parsed_positionally():
    """无表头表格按列序解析（含「事件」字样的数据行不得被当成表头吞掉）。"""
    report = (
        "# 已识别的重大事件\n"
        "| 事件 A 落地 | 货币政策 | 流动性收紧 | 利空 | 70% |\n"
        "| 事件 B 升级 | 地缘政治 | 避险上升 | 利空 | 60% |\n"
    )
    events = parse_identified_events(report)
    assert [e["title"] for e in events] == ["事件 A 落地", "事件 B 升级"]
    assert events[0]["confidence"] == pytest.approx(0.70)


# ==================== 结构化事件字段 ====================

def test_build_market_events_fields_and_prefetch_stats_only():
    prefetch = run_prefetch(news=NEWS, behaviour={"美联储加息 25bp": impact_result()})
    events = build_international_events(REPORT, prefetch, TRADE_DATE)
    assert len(events) == 2
    event = events[0]

    # 字段完备性（方案第五章）
    assert event["event_id"].startswith("全球新闻:")
    assert event["event_scope"] == "market"
    assert event["affected_scope_refs"] == []
    assert event["event_type"] == "货币政策"
    assert event["judgement"].startswith("全球流动性收紧")
    assert event["direction"] == "利空"
    assert event["event_time"] == "2026-08-07T21:00:00"
    assert event["fact"] == "美联储加息 25bp"
    assert event["confidence"] == pytest.approx(0.75)
    assert event["source"] == "全球新闻"
    assert event["history_match_status"] == "ok"
    assert event["data_quality"]["as_of"] == TRADE_DATE
    assert event["data_quality"]["time_precision"] == "datetime"
    assert event["data_quality"]["history_basis"] == "event_study_prefetch"

    # 历史统计只引用预取结果：报告里写的 CAR -8.8% 不得进统计字段
    impact = event["historical_impact"]
    assert impact["weighted_car"] == pytest.approx(-0.0089)
    assert impact["avg_car"] == pytest.approx(-0.0123)
    assert impact["sample_count"] == 12
    assert impact["win_rate"] == pytest.approx(0.3333)
    assert impact["window_type"] == "post_event_5d"
    assert "8.8" not in json.dumps(impact, ensure_ascii=False)


def test_unmatched_event_has_null_impact_and_reason():
    prefetch = run_prefetch(news=NEWS, behaviour={"美联储加息 25bp": impact_result()})
    events = build_international_events(REPORT, prefetch, TRADE_DATE)
    unmatched = events[1]
    assert unmatched["fact"] == "中东地缘冲突升级"
    assert unmatched["history_match_status"] == "unmatched"
    assert unmatched["historical_impact"] is None
    assert any("未匹配到预取候选" in note for note in unmatched["data_quality"]["notes"])
    assert any("不宣称市场已定价" in note for note in unmatched["data_quality"]["notes"])


def test_no_sample_event_has_null_impact_and_reason():
    prefetch = run_prefetch(news=NEWS)      # 全部 no_sample
    events = build_international_events(REPORT, prefetch, TRADE_DATE)
    event = events[0]
    assert event["history_match_status"] == "no_sample"
    assert event["historical_impact"] is None
    assert any("暂无" in note for note in event["data_quality"]["notes"])


def test_build_events_without_prefetch_degrades_to_unmatched():
    events = build_international_events(REPORT, None, TRADE_DATE)
    assert len(events) == 2
    assert all(e["historical_impact"] is None for e in events)
    assert all(e["history_match_status"] == "unmatched" for e in events)
    assert all(e["data_quality"]["notes"] for e in events)


def test_max_five_events():
    rows = "".join(
        f"| 事件{day} | 货币政策 | 全球流动性收紧 | 利空 | 60% |\n" for day in range(1, 8)
    )
    report = f"# 已识别的重大事件\n\n| 事件 | 类型 | 判断 | 影响方向 | 置信度 |\n{rows}"
    events = build_international_events(report, None, TRADE_DATE)
    assert len(events) == 5


# ==================== 市场确认证据（预期差） ====================

def test_market_confirmation_from_macro_release_surprise():
    news = "- [2026-08-07 10:00] 中国 8 月 CPI 超预期\n"
    macro = "# 关键宏观指标\n- 中国 8 月 CPI: 实际 2.5%，预期 2.2%，前值 2.4%\n"
    report = (
        "# 已识别的重大事件\n"
        "| 事件 | 类型 | 判断 | 影响方向 | 置信度 |\n"
        "| 中国 8 月 CPI 超预期 | 宏观数据 | 通胀回升 | 利空 | 70% |\n"
    )
    prefetch = run_prefetch(news=news, macro=macro,
                            behaviour={"中国 8 月 CPI 超预期": impact_result()})
    events = build_international_events(report, prefetch, TRADE_DATE)
    confirmation = events[0]["market_confirmation"]
    assert confirmation["basis"] == "macro_release_surprise"
    assert confirmation["indicator"] == "中国 8 月 CPI"
    assert confirmation["actual"] == "2.5"
    assert confirmation["expected"] == "2.2"
    assert confirmation["surprise"] == "超预期"
    assert not any("不宣称市场已定价" in note
                   for note in events[0]["data_quality"]["notes"])


def test_missing_confirmation_does_not_claim_priced_in():
    prefetch = run_prefetch(news=NEWS, macro="", behaviour={"美联储加息 25bp": impact_result()})
    events = build_international_events(REPORT, prefetch, TRADE_DATE)
    assert events[0]["market_confirmation"] is None
    assert any("不宣称市场已定价" in note for note in events[0]["data_quality"]["notes"])

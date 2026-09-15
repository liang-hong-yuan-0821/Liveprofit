"""
单元测试：市场层事件研究预取（T4；纯函数 + fake conn/预测器，不依赖真实 LLM / 网络）。

覆盖：时点防前视（`event_time <= trade_date`，日期精度不足保守排除同日）、
去重（ID / 来源+标题+时间）、固定窗口（000300.SH + post_event_5d）、
每层最多 5 条、单条失败降级不阻塞、Prompt 数据块只含预取统计。
"""

import pytest

from AI.marketAgents.analysts.international_event_prefetch import (
    build_market_confirmation, extract_macro_releases, extract_text_candidates,
    prefetch_event_study_evidence,
)
from AI.utils.event_prefetch_core import (
    STATUS_API_UNAVAILABLE, STATUS_EMPTY, STATUS_NO_SAMPLE, STATUS_OK, STATUS_PARTIAL,
    render_prefetch_block,
)

TRADE_DATE = "2026-08-08"
RAW_NEWS = (
    "# 全球宏观财经快讯（财联社电报）\n"
    "\n"
    "- [08-08 09:30] 央行宣布全面降准 0.5 个百分点\n"
    "- [08-07 21:00] 美联储官员释放鸽派信号\n"
    "- [08-09 08:00] 明日将公布重要数据\n"
    "- 网传：本次降准对市场影响 CAR 达 88%（无发布时间）\n"
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """最小 PG 连接替身：只记录执行过的 SQL，返回预置行。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return FakeCursor(self.rows)

    def close(self):
        self.closed = True


def impact_result(*, samples=12, template_samples=10, supplement_samples=2,
                  avg_car=-0.0123, weighted_car=-0.0089, win_rate=0.3333,
                  confidence=0.42, status=STATUS_OK, contaminated=False,
                  contaminated_count=1, unavailable_channels=None, reason=None,
                  note=""):
    """predict_impact 返回值最小替身（字段与 T3 交付一致）。"""
    return {
        "prediction": {
            "window_type": "post_event_5d",
            "asset_ticker": "000300.SH",
            "predicted_direction": -1,
            "predicted_return": weighted_car,
            "confidence": confidence,
        },
        "template_stats": {
            "sample_count": template_samples, "avg_car": avg_car, "win_rate": win_rate,
        },
        "supplement_events": [],
        "note": note,
        "sample_metadata": {
            "as_of": None,
            "event_scope": "market",
            "scope_refs": [],
            "sample_count": samples,
            "template_sample_count": template_samples,
            "supplement_sample_count": supplement_samples,
            "contaminated_sample_count": contaminated_count,
            "contaminated": contaminated,
            "history_match_status": status,
            "unavailable_channels": unavailable_channels or [],
            "reason": reason,
        },
    }


def fake_predictor(behaviour):
    """behaviour: {标题: 结果 dict / Exception}；未列出 → no_sample。"""
    calls = []

    def predict(conn, text, asset, **kwargs):
        calls.append({"text": text, "asset": asset, **kwargs})
        value = behaviour.get(text)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return impact_result(samples=0, template_samples=0, supplement_samples=0,
                                 avg_car=None, weighted_car=None, win_rate=None,
                                 confidence=0.0, status=STATUS_NO_SAMPLE,
                                 contaminated=False, contaminated_count=0,
                                 reason="事件库暂无该资产/作用域下的已审核样本")
        return value

    return predict, calls


def fake_route_lister(rows):
    calls = []

    def lister(conn, route, as_of, limit=20):
        calls.append({"route": route, "as_of": as_of, "limit": limit})
        return rows

    return lister, calls


def prefetch(**kwargs):
    kwargs.setdefault("raw_news", RAW_NEWS)
    kwargs.setdefault("central_bank_calendar", "")
    kwargs.setdefault("macro_indicators", "")
    kwargs.setdefault("trade_date", TRADE_DATE)
    return prefetch_event_study_evidence(**kwargs)


# ==================== 时点防前视 ====================

def test_text_candidates_parse_cls_telegraph_time():
    """财联社电报形态「[08-08 09:30] 标题」→ 补年份并解析出时刻。"""
    candidates = extract_text_candidates(RAW_NEWS, TRADE_DATE, "全球新闻")
    by_title = {c["title"]: c for c in candidates}
    # 行首时间戳括号已剥离（时间另存 event_time，不混入标题/事实文本）
    assert "央行宣布全面降准 0.5 个百分点" in by_title
    assert by_title["央行宣布全面降准 0.5 个百分点"]["event_time"] == "2026-08-08T09:30:00"
    assert by_title["美联储官员释放鸽派信号"]["event_time"] == "2026-08-07T21:00:00"


def test_range_text_not_misparsed_as_date():
    """正文区间（如「1-5 交易日」）不得被误判为事件时间。"""
    candidates = extract_text_candidates("- 短线候选板块（1-5 交易日）看好电子", TRADE_DATE, "全球新闻")
    assert candidates and candidates[0]["time_precision"] == "none"


def test_time_gate_blocks_future_and_same_day_date_precision():
    """仅接受 event_time <= trade_date；日期精度不足（无时刻）保守排除同日。"""
    predict, calls = fake_predictor({"央行宣布降准": impact_result()})
    result = prefetch(
        raw_news=(
            "- [2026-08-09 09:00] 晚于交易日的事件\n"
            "- [2026-08-08] 同日（无时刻，保守排除）\n"
            "- [2026-08-08 09:30] 同日（有时刻，可见）\n"
            "- [2026-08-07] 前一日（仅日期，可见）\n"
        ),
        predict_fn=predict, conn=FakeConn(),
    )
    titles = [c["title"] for c in result["candidates"]]
    assert "晚于交易日的事件" not in titles
    assert "同日（无时刻，保守排除）" not in titles
    assert "同日（有时刻，可见）" in titles
    assert "前一日（仅日期，可见）" in titles
    assert result["filter_stats"]["future"] == 2      # 未来 1 条 + 同日无时刻 1 条
    assert len(calls) == 2


def test_predict_as_of_is_event_time():
    """每次预测请求传 as_of=事件自身时间（防前视）。"""
    predict, calls = fake_predictor({"央行宣布降准": impact_result()})
    prefetch(raw_news="- [2026-08-07 21:00] 央行宣布降准\n",
             predict_fn=predict, conn=FakeConn())
    assert calls and calls[0]["as_of"] == "2026-08-07T21:00:00"
    assert calls[0]["event_scope"] == "market"
    assert calls[0]["scope_refs"] == ()
    # 纯文本候选无事件 ID → 不排除（评审 m14 仅在路由候选上生效）
    assert calls[0]["exclude_event_id"] is None


def test_undated_candidate_excluded_and_counted():
    predict, calls = fake_predictor({})
    result = prefetch(raw_news="- 无时间信息的传闻\n", predict_fn=predict, conn=FakeConn())
    assert result["status"] == STATUS_EMPTY
    assert result["filter_stats"]["undated"] == 1
    assert calls == []
    assert "无可用时间被排除" in result["reason"]


# ==================== 固定窗口与去重 ====================

def test_fixed_asset_and_window():
    predict, calls = fake_predictor({"央行宣布降准": impact_result()})
    result = prefetch(raw_news="- [2026-08-07 21:00] 央行宣布降准\n",
                      predict_fn=predict, conn=FakeConn())
    assert calls[0]["asset"] == "000300.SH"
    assert calls[0]["window_type"] == "post_event_5d"
    assert result["asset_tickers"] == ["000300.SH"]
    assert result["window_type"] == "post_event_5d"


def test_dedupe_by_title_and_time_across_sources():
    """同标题同时间（跨来源）合并；同标题不同时间保留。"""
    predict, calls = fake_predictor({
        "央行宣布降准": impact_result(),
    })
    result = prefetch(
        raw_news="- [2026-08-07 21:00] 央行宣布降准\n- [2026-08-07 21:00] 央行宣布降准\n",
        central_bank_calendar="- [2026-08-07 21:00] 央行宣布降准\n",
        predict_fn=predict, conn=FakeConn(),
    )
    assert result["candidate_count"] == 1
    assert len(calls) == 1
    assert result["filter_stats"]["deduped"] == 2


def test_dedupe_by_event_id_from_route():
    rows = [
        {"event_id": 7, "title": "央行宣布降准", "content": "降准 0.5 个百分点",
         "event_type": "央行政策", "event_subtype": None, "event_condition": None,
         "announced_at": "2026-08-07T21:00:00+08:00", "trading_day": None,
         "importance": 5, "event_scope": "market", "affected_scope_refs": [],
         "source_url": "https://example.com/1"},
        {"event_id": 7, "title": "央行宣布降准（重复行）", "content": "",
         "event_type": "央行政策", "event_subtype": None, "event_condition": None,
         "announced_at": "2026-08-07T21:00:00+08:00", "trading_day": None,
         "importance": 5, "event_scope": "market", "affected_scope_refs": [],
         "source_url": "https://example.com/1"},
    ]
    lister, lister_calls = fake_route_lister(rows)
    predict, calls = fake_predictor({"央行宣布降准": impact_result()})
    result = prefetch(raw_news="", predict_fn=predict, conn=FakeConn(), route_lister=lister)
    assert [c["event_id"] for c in result["candidates"]] == [7]
    assert result["candidates"][0]["candidate_id"] == "event:7"
    assert result["candidates"][0]["source"] == "事件库"
    assert len(calls) == 1
    assert lister_calls[0]["as_of"] == TRADE_DATE
    # 路由候选带 event_id → 取统计时排除自身（评审 m14，避免自相关污染）
    assert calls[0]["exclude_event_id"] == 7


# ==================== 每层最多 5 条 ====================

def test_at_most_five_candidates_most_recent_first():
    predict, calls = fake_predictor({})
    lines = "".join(
        f"- [2026-08-0{day} 10:00] 事件{day}\n" for day in range(1, 9)
    )
    result = prefetch(raw_news=lines, predict_fn=predict, conn=FakeConn())
    assert result["candidate_count"] == 5
    assert [c["title"] for c in result["candidates"]] == [
        "事件8", "事件7", "事件6", "事件5", "事件4",
    ]
    assert len(calls) == 5


# ==================== 失败降级（不阻塞） ====================

def test_single_failure_degrades_without_blocking():
    predict, calls = fake_predictor({
        "央行宣布降准": impact_result(),
        "数据不及预期": RuntimeError("事件研究 API 超时"),
    })
    result = prefetch(
        raw_news=(
            "- [2026-08-07 21:00] 央行宣布降准\n"
            "- [2026-08-07 20:00] 数据不及预期\n"
            "- [2026-08-07 19:00] 无历史样本的事件\n"
        ),
        predict_fn=predict, conn=FakeConn(),
    )
    assert result["status"] == STATUS_PARTIAL
    assert len(result["candidates"]) == 3
    statuses = {c["title"]: c["history_match_status"] for c in result["candidates"]}
    assert statuses["央行宣布降准"] == STATUS_OK
    assert statuses["数据不及预期"] == STATUS_API_UNAVAILABLE
    assert statuses["无历史样本的事件"] == STATUS_NO_SAMPLE
    failed = next(c for c in result["candidates"] if c["title"] == "数据不及预期")
    assert failed["historical_impact"] is None
    assert "事件研究查询失败" in failed["reason"]


def test_all_failures_return_api_unavailable():
    predict, _ = fake_predictor({
        "事件A": RuntimeError("boom"),
        "事件B": RuntimeError("boom"),
    })
    result = prefetch(raw_news="- [2026-08-07 21:00] 事件A\n- [2026-08-07 20:00] 事件B\n",
                      predict_fn=predict, conn=FakeConn())
    assert result["status"] == STATUS_API_UNAVAILABLE
    assert all(c["historical_impact"] is None for c in result["candidates"])


def test_no_samples_return_no_sample_status():
    predict, _ = fake_predictor({})
    result = prefetch(raw_news="- [2026-08-07 21:00] 冷启动事件\n",
                      predict_fn=predict, conn=FakeConn())
    assert result["status"] == STATUS_NO_SAMPLE
    assert result["candidates"][0]["history_match_status"] == STATUS_NO_SAMPLE
    assert result["candidates"][0]["historical_impact"] is None


def test_single_failure_rolls_back_before_next_query():
    """单条 predict 失败先 `conn.rollback()`（评审 M4）。

    本地 PG 连接 autocommit=False：失败语句把事务置为 aborted，不回滚则
    后续所有查询都以 "current transaction is aborted" 连坐失败并被吞成
    api_unavailable（历史统计整体失真）。
    """
    order = []

    class RollbackConn(FakeConn):
        def rollback(self):
            order.append("rollback")

    def predict(conn, text, asset, **kwargs):
        order.append(text)
        if "失败事件" in text:
            raise RuntimeError("current transaction is aborted")
        return impact_result()

    result = prefetch(
        raw_news="- [2026-08-07 21:00] 失败事件\n- [2026-08-07 20:00] 正常事件\n",
        predict_fn=predict, conn=RollbackConn(),
    )
    assert "rollback" in order, "单条失败后必须回滚"
    assert order.index("rollback") < order.index("正常事件"), "回滚必须发生在下一条查询之前"
    assert result["status"] == STATUS_PARTIAL
    failed = next(c for c in result["candidates"] if c["title"] == "失败事件")
    assert failed["historical_impact"] is None
    assert "current transaction is aborted" in failed["reason"]


def test_safe_rollback_never_raises():
    """`safe_rollback` 尽力回滚：连接为 None / rollback 自身抛错都不得再抛（M4 残留）。"""
    from AI.utils.event_prefetch_core import safe_rollback

    calls = []

    class BrokenConn:
        def rollback(self):
            calls.append("rollback")
            raise RuntimeError("连接已断")

    safe_rollback(BrokenConn())  # 不抛（异常降级路径上不得覆盖原始失败原因）
    safe_rollback(None)          # 不抛
    assert calls == ["rollback"]


def test_partial_status_keeps_first_error_reason():
    """部分候选有统计时首错原因仍须透出（M4 残留）：reason 非空、可核对。"""
    predict, _ = fake_predictor({
        "事件A": impact_result(),
        "事件B": RuntimeError("boom"),
    })
    result = prefetch(raw_news="- [2026-08-07 21:00] 事件A\n- [2026-08-07 20:00] 事件B\n",
                      predict_fn=predict, conn=FakeConn())
    assert result["status"] == STATUS_PARTIAL
    assert result["reason"] and "boom" in result["reason"]


def test_first_failure_reason_recorded_in_aggregate():
    """全部失败 → api_unavailable 且整体原因带**首个**异常（评审 M4）。"""
    class RollbackConn(FakeConn):
        def rollback(self):
            pass

    def predict(conn, text, asset, **kwargs):
        raise RuntimeError("connection already closed")

    result = prefetch(
        raw_news="- [2026-08-07 21:00] 事件A\n- [2026-08-07 20:00] 事件B\n",
        predict_fn=predict, conn=RollbackConn(),
    )
    assert result["status"] == STATUS_API_UNAVAILABLE
    assert "首个失败原因" in result["reason"]
    assert "connection already closed" in result["reason"]


def test_route_query_failure_does_not_block_text_candidates():
    def failing_lister(conn, route, as_of, limit=20):
        raise RuntimeError("PG 不可用")

    predict, calls = fake_predictor({"央行宣布降准": impact_result()})
    result = prefetch(raw_news="- [2026-08-07 21:00] 央行宣布降准\n",
                      predict_fn=predict, conn=FakeConn(),
                      route_lister=failing_lister)
    assert result["status"] == STATUS_OK
    assert result["route_error"] and "路由事件查询失败" in result["route_error"]
    assert result["candidate_sources"]["route_events"] == 0


def test_route_query_failure_rolls_back_first():
    """路由查询失败同样先回滚（M4 残留）：失败语句中毒事务，不回滚会连坐后续查询。"""
    from AI.utils.event_prefetch_core import fetch_route_candidates

    order = []

    class RollbackConn(FakeConn):
        def rollback(self):
            order.append("rollback")

    def failing_lister(conn, route, as_of, limit=20):
        order.append("query")
        raise RuntimeError("PG 不可用")

    candidates, error = fetch_route_candidates(
        RollbackConn(), route=object(), trade_date=TRADE_DATE,
        route_lister=failing_lister,
    )
    assert candidates == []
    assert "路由事件查询失败" in error
    assert order == ["query", "rollback"]  # 回滚发生在返回降级结果之前


# ==================== Prompt 数据块 ====================

def test_prompt_block_only_contains_prefetch_stats():
    """组装后 Prompt 文本不含输入外统计（新闻正文里的「CAR 88%」不得进块）。"""
    predict, _ = fake_predictor({"央行宣布全面降准 0.5 个百分点": impact_result()})
    result = prefetch(raw_news=RAW_NEWS, predict_fn=predict, conn=FakeConn())
    block = render_prefetch_block(result)
    assert "样本数: 12" in block
    assert "-0.89%" in block          # 加权 CAR 来自预测器返回值
    assert "-1.23%" in block          # 模板平均 CAR
    assert "33.3%" in block           # 胜率
    assert "事件研究历史统计（预取）" in block
    assert "88%" not in block         # 输入文本中的统计不冒充 CAR 证据
    assert "网传" not in block         # 无时间行不进候选，其正文不进历史统计块


def test_prompt_block_reports_unparsable_trade_date_coverage():
    """交易日不可解析 → 被排除候选的覆盖统计必须进 Prompt 块（m15 残留）。

    否则预取「静默为空」，Prompt 无法区分「事件库确无事件」与「时点不可用」。
    """
    predict, calls = fake_predictor({})
    result = prefetch(raw_news="- [2026-08-07 21:00] 事件A\n",
                      predict_fn=predict, conn=FakeConn(),
                      trade_date="交易日待定")
    assert result["filter_stats"]["unparsed"] == 1
    assert result["candidates"] == []
    assert calls == []
    block = render_prefetch_block(result)
    assert "1 条候选因交易日不可解析无法判定可见性被排除" in block


def test_prompt_block_has_no_numbers_when_no_sample():
    predict, _ = fake_predictor({})
    result = prefetch(raw_news="- [2026-08-07 21:00] 冷启动事件\n",
                      predict_fn=predict, conn=FakeConn())
    block = render_prefetch_block(result)
    assert "%" not in block
    assert "不得由模型补写数值" in block


# ==================== 宏观发布预期差（市场确认证据） ====================

def test_extract_macro_releases_surprise_direction():
    text = (
        "# 关键宏观经济指标\n"
        "- 中国 8 月 CPI: 实际 2.5%，预期 2.2%，前值 2.4%\n"
        "- 美国 8 月非农: 实际 12.0，预期 15.0，前值 14.0\n"
    )
    releases = extract_macro_releases(text)
    assert [r["indicator"] for r in releases] == ["中国 8 月 CPI", "美国 8 月非农"]
    assert releases[0]["surprise"] == "超预期"
    assert releases[1]["surprise"] == "不及预期"


def test_market_confirmation_requires_surprise():
    releases = extract_macro_releases("- 中国 8 月 CPI: 实际 2.5%，预期 2.2%，前值 2.4%\n")
    confirmation = build_market_confirmation("中国 8 月 CPI 超预期", releases)
    assert confirmation["basis"] == "macro_release_surprise"
    assert confirmation["surprise"] == "超预期"
    # 无预期差数据 → 不宣称已定价
    assert build_market_confirmation("地缘冲突升级", releases) is None
    assert build_market_confirmation("中国 8 月 CPI", []) is None

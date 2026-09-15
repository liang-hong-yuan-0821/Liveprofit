"""
板块轮动预测分析师测试。

① node 级单测（mock dataflow 三个函数 + mock LLM）：
   - 门控三态：全不可用短路占位 / 部分可用占位注入 / 全可用
   - 单块抛异常 → 该块不可用（原因串记异常信息）
   - 异常型错误串（如"获取xx失败: …"）不以 # 开头 → 不误判为可用
② 集成测试（真实 LLM + Tushare，仅全可用路径）：
   - 真实数据下"部分可用"态无法确定性构造，仅覆盖全可用路径
"""

from unittest.mock import MagicMock

import pytest

from AI.dataflows import interface as dataflow
from AI.sectorAgents.analysts.sector_rotation_analyst import create_sector_rotation_analyst

ROTATION_OK = "# 题材板块近 10 日逐日轮动矩阵\n| 20260819 | AI眼镜(涨停8/首板) |"
CONCEPT_OK = "# 近 10 日东财概念板块逐日涨幅 TOP20 矩阵\n| 20260819 | AI眼镜(+10.0%/8.2%) |"
LADDER_OK = "# 近 20 日连板梯队与情绪数据\n| 20260819 | 36 | 118 | 23 |"
INDUSTRY_PERF_OK = "# 全行业板块涨跌排名（近 10 日）\n| 1 | 银行 | +2.00% |"
INDUSTRY_FLOW_OK = "# 行业板块资金流向排名（近 5 日）\n| 1 | 银行 | +10.5亿 |"
INDUSTRY_PERF_NA = "数据不可用：行业排名未配置。"
INDUSTRY_FLOW_NA = "数据不可用：资金流未配置。"


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-19",
        "messages": [],
        "rotation_tool_call_count": 0,
        # T6：market_regime 为结构化 dict（原 str 原地替换）
        "market_regime": {
            "short_term": {"level": "适合"},
            "wave": {"level": "进攻"},
            "long_term": {"level": "配置窗口"},
        },
        "sector_news_report": "",
        "sector_tech_report": "",
    }


class _FakeLLM:
    """捕获 system prompt 的假 LLM，返回固定报告"""

    def __init__(self, content: str = "# 测试报告\n预测内容"):
        self.content = content
        self.system_prompt = ""

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        self.system_prompt = messages[0].content
        return AIMessage(content=self.content)


def _mock_dataflow(monkeypatch, rotation=None, concept_top=None, ladder=None,
                   industry_perf=None, industry_flow=None):
    """mock dataflow 各块函数；值为 Exception 时模拟抛异常。

    行业基本面块由 get_industry_sector_performance + get_sector_fund_flow
    拼接而成，默认均不可用（不传时）。同时 mock 热力图生成函数——
    其依赖全局 _run.log_dir（同一 pytest 会话下可能被其他测试写入 tmp
    目录），不 mock 时 node 单测会发真实数据请求。
    """
    def set_attr(name, value):
        if isinstance(value, Exception):
            def raiser(*args, **kwargs):
                raise value
            monkeypatch.setattr(dataflow, name, raiser)
        else:
            monkeypatch.setattr(dataflow, name, MagicMock(return_value=value))

    set_attr("get_concept_rotation_ranking", rotation)
    set_attr("get_concept_daily_top_gains", concept_top)
    set_attr("get_limit_up_ladder", ladder)
    set_attr("get_industry_sector_performance",
             industry_perf if industry_perf is not None else INDUSTRY_PERF_NA)
    set_attr("get_sector_fund_flow",
             industry_flow if industry_flow is not None else INDUSTRY_FLOW_NA)

    from AI.sectorAgents import charts as charts_mod
    monkeypatch.setattr(charts_mod, "generate_sector_heatmaps",
                        MagicMock(return_value=None))


def _make_node(llm=None):
    return create_sector_rotation_analyst(llm or _FakeLLM(), MagicMock())


# ==================== 门控三态 ====================

def test_all_unavailable_short_circuits(monkeypatch, state):
    """四块全不可用 → 不调 LLM，占位原因按块分行拼接"""
    llm = _FakeLLM()
    _mock_dataflow(
        monkeypatch,
        rotation="数据不可用：Tushare 不支持 题材板块轮动矩阵。",
        concept_top="近 10 个交易日无概念板块快照数据。",
        ladder="Tushare 未连接。",
    )

    result = _make_node(llm)(state)

    placeholder = result["rotation_prediction_report"]
    assert placeholder == (
        "(数据不可用，跳过板块轮动预测。原因：\n"
        "- 轮动矩阵：数据不可用：Tushare 不支持 题材板块轮动矩阵。\n"
        "- 概念涨幅：近 10 个交易日无概念板块快照数据。\n"
        "- 连板梯队：Tushare 未连接。\n"
        "- 行业基本面：数据不可用：行业排名未配置。\n"
        "\n"
        "数据不可用：资金流未配置。)"
    )
    assert result["rotation_top_picks"] == placeholder
    assert result["messages"] == []
    assert result["rotation_tool_call_count"] == 1
    assert llm.system_prompt == ""          # LLM 未被调用


def test_partial_available_injects_placeholder(monkeypatch, state):
    """部分可用 → 正常分析，缺失块以 '(数据不可用：<原因>)' 占位注入"""
    llm = _FakeLLM()
    _mock_dataflow(
        monkeypatch,
        rotation=ROTATION_OK,
        concept_top="近 10 个交易日无概念板块快照数据。",
        ladder=LADDER_OK,
    )

    result = _make_node(llm)(state)

    assert "rotation_prediction_report" in result
    assert ROTATION_OK in llm.system_prompt
    assert LADDER_OK in llm.system_prompt
    # 缺失块占位注入（注入格式紧跟在数据段标题后）
    assert ("### 东财概念逐日涨幅 TOP20 矩阵（近10日）\n"
            "(数据不可用：近 10 个交易日无概念板块快照数据。)") in llm.system_prompt


def test_all_available(monkeypatch, state):
    """四块全可用 → 四块原始内容全部进入 prompt，无占位"""
    llm = _FakeLLM()
    _mock_dataflow(monkeypatch, rotation=ROTATION_OK,
                   concept_top=CONCEPT_OK, ladder=LADDER_OK,
                   industry_perf=INDUSTRY_PERF_OK, industry_flow=INDUSTRY_FLOW_OK)

    result = _make_node(llm)(state)

    assert result["rotation_prediction_report"] == llm.content
    # 四块原始内容直接跟在各自数据段标题后（无占位注入）
    assert "### 题材板块逐日轮动矩阵\n" + ROTATION_OK in llm.system_prompt
    assert "### 东财概念逐日涨幅 TOP20 矩阵（近10日）\n" + CONCEPT_OK in llm.system_prompt
    assert "### 全市场连板梯队与情绪数据（近20日）\n" + LADDER_OK in llm.system_prompt
    assert ("### 行业基本面（近10日行业涨跌排名 + 近5日行业资金流向）\n"
            + INDUSTRY_PERF_OK + "\n\n" + INDUSTRY_FLOW_OK) in llm.system_prompt
    # T6：结构化 dict 大盘环境经紧凑渲染注入（原 str 拼接/长度判定已删）
    assert "## 大盘环境判定（市场层）" in llm.system_prompt
    assert "短线(5日)：适合" in llm.system_prompt


def test_market_regime_absent_falls_back_to_unavailable(state):
    """market_regime 缺失/非 dict → 不注入（不以 str 长度命中），上下文判不可用。"""
    from AI.sectorAgents.analysts.sector_rotation_analyst import _build_sector_context

    for missing in ({}, "", None):
        context = _build_sector_context({**state, "market_regime": missing})
        assert "大盘环境判定" not in context
        assert context == "（板块层数据暂不可用）"


def test_industry_only_available(monkeypatch, state):
    """仅行业基本面可用 → 该块进入 prompt，其余三块占位"""
    llm = _FakeLLM()
    _mock_dataflow(
        monkeypatch,
        rotation="数据不可用：仅 Tushare 支持。",
        concept_top="数据不可用：仅 Tushare 支持。",
        ladder="数据不可用：仅 Tushare 支持。",
        industry_perf=INDUSTRY_PERF_OK,
        industry_flow=INDUSTRY_FLOW_OK,
    )

    result = _make_node(llm)(state)

    assert "rotation_prediction_report" in result
    assert ("### 行业基本面（近10日行业涨跌排名 + 近5日行业资金流向）\n"
            + INDUSTRY_PERF_OK) in llm.system_prompt
    assert "(数据不可用：数据不可用：仅 Tushare 支持。)" in llm.system_prompt


def test_industry_only_unavailable(monkeypatch, state):
    """仅行业基本面不可用（其余可用）→ 行业块占位注入，不拖累其余块"""
    llm = _FakeLLM()
    _mock_dataflow(monkeypatch, rotation=ROTATION_OK,
                   concept_top=CONCEPT_OK, ladder=LADDER_OK)

    result = _make_node(llm)(state)

    assert "rotation_prediction_report" in result
    assert ROTATION_OK in llm.system_prompt
    assert ("### 行业基本面（近10日行业涨跌排名 + 近5日行业资金流向）\n"
            "(数据不可用：数据不可用：行业排名未配置。\n"
            "\n"
            "数据不可用：资金流未配置。)") in llm.system_prompt


# ==================== 异常与契约 ====================

def test_exception_treated_unavailable(monkeypatch, state):
    """单块抛异常 → 该块不可用（原因串记异常信息），node 不崩溃"""
    llm = _FakeLLM()
    _mock_dataflow(
        monkeypatch,
        rotation=ROTATION_OK,
        concept_top=RuntimeError("boom"),
        ladder=LADDER_OK,
    )

    result = _make_node(llm)(state)

    assert "rotation_prediction_report" in result
    assert "(数据不可用：获取数据异常: boom)" in llm.system_prompt


def test_error_string_not_misjudged_as_available(monkeypatch, state):
    """异常型错误串（'获取xx失败: …'）不以 # 开头 → 判定为不可用，不误入可用块"""
    llm = _FakeLLM()
    _mock_dataflow(
        monkeypatch,
        rotation="获取题材板块数据失败: timeout",
        concept_top=CONCEPT_OK,
        ladder=LADDER_OK,
    )

    result = _make_node(llm)(state)

    assert "rotation_prediction_report" in result
    assert "(数据不可用：获取题材板块数据失败: timeout)" in llm.system_prompt
    # 错误串本身不得作为数据块直接注入
    assert "获取题材板块数据失败: timeout" not in llm.system_prompt.replace(
        "(数据不可用：获取题材板块数据失败: timeout)", ""
    )


def test_none_result_treated_unavailable(monkeypatch, state):
    """dataflow 返回 None/空串 → 按不可用处理"""
    llm = _FakeLLM()
    _mock_dataflow(monkeypatch, rotation=None, concept_top="", ladder=LADDER_OK)

    result = _make_node(llm)(state)

    assert "rotation_prediction_report" in result
    assert "(数据不可用：返回为空)" in llm.system_prompt


def test_report_and_top_picks_populated(monkeypatch, state):
    llm = _FakeLLM(content="# 板块轮动预测报告\n## 〇、轮动预测速览\n```\n明日主线预测: AI眼镜\n```")
    _mock_dataflow(monkeypatch, rotation=ROTATION_OK,
                   concept_top=CONCEPT_OK, ladder=LADDER_OK)

    result = _make_node(llm)(state)

    assert "轮动预测速览" in result["rotation_prediction_report"]
    assert "明日主线预测" in result["rotation_top_picks"]
    assert result["rotation_tool_call_count"] == 1


# ==================== 集成测试（真实 LLM + Tushare） ====================

def test_integration_generates_report(real_llm, real_toolkit, state):
    """真实运行：三块数据全可用路径，产出轮动预测报告 + 速览短名单"""
    node = create_sector_rotation_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "rotation_prediction_report" in result
    assert len(result["rotation_prediction_report"]) > 100
    assert "rotation_top_picks" in result
    assert len(result["rotation_top_picks"]) > 0
    assert result["rotation_tool_call_count"] >= 1

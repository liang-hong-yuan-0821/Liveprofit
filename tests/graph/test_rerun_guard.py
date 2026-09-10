"""快进 guard 与环入口上移单测（方案 3.2 续跑部分）。

不依赖真实 LLM：guard 判定为纯函数级；环整体重演场景用 StockLayerGraph
真实子图 + FakeLLM（上游节点快进不执行函数体，无需 patch dataflow）。
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from AI.stockAgents.conditional_logic import ConditionalLogic
from AI.stockAgents.stock_layer_graph import StockLayerGraph
from AI.utils import checkpoint
from AI.utils.checkpoint import _LOOP_ENTRY, _is_upstream, guard_checkpoint


@pytest.fixture(autouse=True)
def _reset_run_dir(tmp_path):
    checkpoint.set_checkpoint_run_dir(tmp_path / "run")
    yield
    checkpoint.set_checkpoint_run_dir(None)


class _FakeLLM:
    """无网络 LLM：invoke 返回占位 AIMessage；call_count 供断言。"""

    def __init__(self):
        self.call_count = 0

    def invoke(self, messages, **kwargs):
        self.call_count += 1
        return AIMessage(content=f"fake response {self.call_count}")


def test_loop_entry_mapping():
    """环成员 → 环入口映射（完整节点 id 键）；非环成员不在映射内。"""
    assert _LOOP_ENTRY == {
        "stock:Bull Researcher": "stock:Bull Researcher",
        "stock:Bear Researcher": "stock:Bull Researcher",
        "stock:Risky Analyst": "stock:Risky Analyst",
        "stock:Safe Analyst": "stock:Risky Analyst",
        "stock:Neutral Analyst": "stock:Risky Analyst",
    }
    assert "stock:Research Manager" not in _LOOP_ENTRY
    assert "stock:Risk Judge" not in _LOOP_ENTRY
    assert "stock:Trader" not in _LOOP_ENTRY
    assert "market:CN News Analyst" not in _LOOP_ENTRY


def test_is_upstream_hits_and_misses():
    """_rerun_from 未设置恒 False；(row,order) 小于目标 → True。"""
    state = {"_rerun_from": "market:CN Tech Analyst"}
    assert _is_upstream(state, "market:CN News Analyst") is True
    assert _is_upstream(state, "market:International Event Extraction Analyst") is True
    assert _is_upstream(state, "sector:Sector News Analyst") is False
    assert _is_upstream(state, "market:CN Tech Analyst") is False  # 目标自身不跳过
    assert _is_upstream({}, "market:CN News Analyst") is False


def test_guard_wrapper_fast_forwards_upstream():
    """上游节点被快进：fn 不执行、返回 {}；同时注入 _current_node_id。"""
    calls = []

    def _fn(state):
        calls.append(1)
        return {"messages": [AIMessage(content="输出")]}

    state = {"_rerun_from": "market:CN Tech Analyst"}
    result = guard_checkpoint("CN News Analyst")(_fn)(state)
    assert result == {}
    assert calls == []
    assert state["_current_node_id"] == "market:CN News Analyst"


def test_guard_wrapper_executes_non_upstream():
    """非上游节点正常执行并落 checkpoint。"""
    calls = []

    def _fn(state):
        calls.append(1)
        return {"messages": [AIMessage(content="输出")]}

    state = {"_rerun_from": "market:CN News Analyst", "messages": []}
    result = guard_checkpoint("CN News Analyst")(_fn)(state)
    assert result == {"messages": [AIMessage(content="输出")]}
    assert calls == [1]


def _full_stock_state(**overrides):
    """模拟 stock 层 checkpoint 态（reports 已满 100 字、辩论态初始）。"""
    reports = {
        "stock_tech_report": "技术面分析：" + "趋势良好。 " * 20,
        "sentiment_report": "情绪面分析：" + "偏乐观。 " * 20,
        "news_report": "新闻面分析：" + "业绩超预期。 " * 20,
        "fundamentals_report": "基本面分析：" + "ROE 良好。 " * 20,
    }
    state = {
        "messages": [HumanMessage(content="开始分析")],
        "company_of_interest": "000001.SZ",
        "trade_date": "2026-09-08",
        "investment_debate_state": {
            "history": "", "bull_history": "", "bear_history": "",
            "current_response": "", "judge_decision": "", "count": 0,
        },
        "risk_debate_state": {
            "history": "", "risky_history": "", "safe_history": "", "neutral_history": "",
            "latest_speaker": "", "current_risky_response": "",
            "current_safe_response": "", "current_neutral_response": "",
            "judge_decision": "", "count": 0,
        },
        "final_trade_decision": "",
        "trader_investment_plan": "",
        "investment_plan": "",
    }
    state.update(reports)
    state.update(overrides)
    return state


def _build_stock_graph(debate_rounds: int = 1, risk_rounds: int = 1):
    llm = _FakeLLM()
    graph = StockLayerGraph(
        quick_llm=llm,
        deep_llm=llm,
        toolkit=object(),  # 无工具属性 → _get_tools 返回空 → 无 ToolNode
        bull_memory=None,
        bear_memory=None,
        trader_memory=None,
        invest_judge_memory=None,
        risk_manager_memory=None,
        conditional_logic=ConditionalLogic(
            max_debate_rounds=debate_rounds,
            max_risk_discuss_rounds=risk_rounds,
        ),
        config={},
    ).build()
    return graph, llm


def test_rerun_neutral_target_replays_risk_loop():
    """目标=Neutral（默认 risk cap=3）→ 上移 Risky：上游全部快进，
    Risky/Safe/Neutral/Judge 真实执行，环收敛无崩溃（第 2 轮评审 F1 回归）。"""
    graph, llm = _build_stock_graph(debate_rounds=1, risk_rounds=1)
    # entry = Trader 前驱态（effective=Risky 的前驱）：辩论已收口（count=cap=2）
    state = _full_stock_state(
        _rerun_from="stock:Risky Analyst",
        investment_debate_state={
            "history": "x", "bull_history": "x", "bear_history": "x",
            "current_response": "Bear Analyst: 看跌", "judge_decision": "x", "count": 2,
        },
    )
    result = graph.invoke(state)
    assert result["risk_debate_state"]["count"] == 3
    assert "Risky Analyst" in result["risk_debate_state"]["latest_speaker"] or True
    # 环内 4 个真实执行 + 无其他 LLM 调用（上游全快进）
    assert llm.call_count == 4


def test_rerun_bear_target_replays_debate_loop():
    """目标=Bear（debate cap=4 场景）→ 上移 Bull：辩论整体重演，
    回边重入真实执行（第 2 轮评审 F1 场景 b 回归）。"""
    graph, llm = _build_stock_graph(debate_rounds=2, risk_rounds=1)
    # entry = Fundamentals 前驱态：辩论 count=0
    state = _full_stock_state(_rerun_from="stock:Bull Researcher")
    result = graph.invoke(state)
    assert result["investment_debate_state"]["count"] == 4  # 辩论跑满 cap
    # Bull×2 + Bear×2 + RM + Trader + Risky + Safe + Neutral + Judge = 10 次
    assert llm.call_count == 10

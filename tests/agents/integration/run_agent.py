"""
Agent 集成测试 —— 真实 LLM + 真实数据源。

运行一个 agent，打印完整的输入/输出/工具调用，用于评估提示词效果。

用法:
    cd Liveprofit
    .venv/Scripts/python.exe tests/agents/integration/run_agent.py cn_news
    .venv/Scripts/python.exe tests/agents/integration/run_agent.py fundamentals
    .venv/Scripts/python.exe tests/agents/integration/run_agent.py trader --stock 000001.SZ
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 加载 .env
from dotenv import load_dotenv
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    load_dotenv(env_file, override=False)
    print(f"[ENV] 已加载: {env_file}")

from langchain_openai import ChatOpenAI
from AI.default_config import load_config
from AI.stockAgents import Toolkit


# ============================================================
# Agent 注册表 —— 每个 agent 的名称、导入路径、工厂函数、所需依赖
# ============================================================

AGENTS = {
    # ---- 市场层 ----
    "cn_news": {
        "module": "AI.marketAgents.analysts.cn_news_analyst",
        "factory": "create_cn_news_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "messages": [],
            "cn_news_tool_call_count": 0,
        },
    },
    "cn_tech": {
        "module": "AI.marketAgents.analysts.cn_tech_analyst",
        "factory": "create_cn_tech_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "messages": [],
            "cn_tech_tool_call_count": 0,
        },
    },
    "international_news": {
        "module": "AI.marketAgents.analysts.international_news_analyst",
        "factory": "create_international_news_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "messages": [],
            "international_news_tool_call_count": 0,
            # T6：事件类证据只消费结构化事件（示例；真实运行由事件提取节点产出）
            "international_events": [{
                "event_scope": "market",
                "fact": "美联储降息 25bp",
                "event_time": "2026-08-07",
                "source": "美联储",
                "affected_scope_refs": ["A 股"],
                "history_match_status": "unmatched",
                "historical_impact": None,
            }],
        },
    },
    "us_news": {
        "module": "AI.marketAgents.analysts.us_news_analyst",
        "factory": "create_us_news_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "messages": [],
            "us_news_tool_call_count": 0,
        },
    },
    # ---- 板块层 ----
    "sector_news": {
        "module": "AI.sectorAgents.analysts.sector_news_analyst",
        "factory": "create_sector_news_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "messages": [],
            "sector_news_tool_call_count": 0,
            # T6：市场层结构化字段为 dict（原 str 原地替换）
            "market_regime": {
                "short_term": {"level": "适合", "confidence": "中"},
                "wave": {"level": "进攻"},
                "long_term": {"level": "配置窗口"},
            },
            "market_event_calendar": {
                "short_term": {"level": "低", "score": 1, "key_dates": []},
                "wave": {"level": "低", "score": 1, "key_dates": []},
                "long_term": {"level": "低", "score": 1, "key_dates": []},
            },
        },
    },
    # ---- 个股层 ----
    "fundamentals": {
        "module": "AI.stockAgents.analysts.fundamentals_analyst",
        "factory": "create_fundamentals_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "company_of_interest": "000001.SZ",
            "messages": [],
            "fundamentals_tool_call_count": 0,
        },
    },
    "market_analyst": {
        "module": "AI.stockAgents.analysts.market_analyst",
        "factory": "create_market_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "company_of_interest": "000001.SZ",
            "messages": [],
            "stock_tech_tool_call_count": 0,
        },
    },
    "news": {
        "module": "AI.stockAgents.analysts.news_analyst",
        "factory": "create_news_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "company_of_interest": "000001.SZ",
            "messages": [],
            "news_tool_call_count": 0,
        },
    },
    "sentiment": {
        "module": "AI.stockAgents.analysts.social_media_analyst",
        "factory": "create_social_media_analyst",
        "deps": ["llm", "toolkit"],
        "state": {
            "trade_date": "2026-08-08",
            "company_of_interest": "000001.SZ",
            "messages": [],
            "sentiment_tool_call_count": 0,
        },
    },
    "trader": {
        "module": "AI.stockAgents.trader.trader",
        "factory": "create_trader",
        "deps": ["llm", "memory"],
        "memory": True,
        "state": {
            "company_of_interest": "000001.SZ",
            "investment_plan": "# 投资计划\n\n波段：买入，目标价15元，止损13.5元",
            "stock_tech_report": "(此处应为技术分析报告)",
            "sentiment_report": "(此处应为情绪报告)",
            "news_report": "(此处应为新闻报告)",
            "fundamentals_report": "(此处应为基本面报告)",
        },
    },
}


def create_llm(config):
    """创建真实 LLM 实例"""
    return ChatOpenAI(
        model=config["quick_think_llm"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        temperature=config["quick_temperature"],
        max_tokens=config["max_tokens"],
        timeout=180,
    )


def run_agent(agent_name, stock_code=None):
    """运行单个 agent 并打印结果"""
    if agent_name not in AGENTS:
        print(f"[ERROR] 未知 agent: {agent_name}")
        print(f"可用: {', '.join(AGENTS.keys())}")
        return

    spec = AGENTS[agent_name]
    config = load_config()

    # ---- 创建 LLM ----
    print(f"\n{'='*60}")
    print(f"Agent: {agent_name}")
    print(f"模型: {config['quick_think_llm']}")
    print(f"API:  {config['base_url']}")
    print(f"{'='*60}")

    llm = create_llm(config)
    toolkit = Toolkit(config=config)

    # 如果指定了 stock_code，覆盖默认值
    state = dict(spec["state"])
    if stock_code and "company_of_interest" in state:
        state["company_of_interest"] = stock_code

    # ---- 导入工厂函数 ----
    mod = __import__(spec["module"], fromlist=[spec["factory"]])
    factory = getattr(mod, spec["factory"])

    # ---- 创建 agent node ----
    if "memory" in spec and spec["memory"]:
        from AI.stockAgents.utils.memory import FinancialSituationMemory
        memory = FinancialSituationMemory("test_memory", config)
        node = factory(llm, memory)
    else:
        node = factory(llm, toolkit)

    print(f"\n>>> 初始 State:")
    for k, v in state.items():
        val = str(v)[:200]
        print(f"    {k}: {val}")

    # ---- 运行 ----
    print(f"\n>>> 调用 LLM ...")
    t_start = datetime.now()
    result = node(state)
    elapsed = (datetime.now() - t_start).total_seconds()

    # ---- 输出 ----
    print(f"\n>>> 耗时: {elapsed:.1f}s")
    print(f"\n>>> 返回字段:")
    for k, v in result.items():
        if k == "messages":
            print(f"    {k}: {len(v)} 条消息")
            for i, msg in enumerate(v):
                msg_type = type(msg).__name__
                content_len = len(str(getattr(msg, 'content', '')))
                tool_calls = getattr(msg, 'tool_calls', [])
                extra = ""
                if tool_calls:
                    extra = f", tool_calls={len(tool_calls)}"
                print(f"      [{i}] {msg_type} ({content_len} chars{extra})")
        else:
            val = str(v)[:300]
            print(f"    {k}: {val}")

    # ---- 打印完整报告 ----
    for key in result:
        if key.endswith("_report") or key.endswith("_plan") or key.endswith("_decision"):
            content = result[key]
            if isinstance(content, str) and len(content) > 50:
                print(f"\n{'─'*60}")
                print(f"📄 {key}")
                print(f"{'─'*60}")
                print(content[:5000])
                if len(content) > 5000:
                    print(f"\n... (截断，共 {len(content)} 字符)")

    return result


def list_agents():
    """列出所有可测试的 agent"""
    print("\n可用的 agent 集成测试:")
    print(f"{'Agent':<25} {'层':<10} {'依赖':<20}")
    print("-" * 55)
    for name, spec in AGENTS.items():
        layer = "market" if name in ("cn_news","cn_tech","international_news","us_news","kr_news","us_tech","kr_tech") else \
                "sector" if name.startswith("sector") else "stock"
        deps = ", ".join(spec["deps"])
        print(f"  {name:<23} {layer:<10} {deps:<20}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行单个 Agent 集成测试")
    parser.add_argument("agent", nargs="?", help="Agent 名称")
    parser.add_argument("--list", action="store_true", help="列出所有可用 agent")
    parser.add_argument("--stock", default=None, help="股票代码，如 000001.SZ")
    args = parser.parse_args()

    if args.list or not args.agent:
        list_agents()
        if not args.agent:
            print("\n用法: python run_agent.py <agent_name> [--stock 000001.SZ]")
            sys.exit(0)

    run_agent(args.agent, stock_code=args.stock)

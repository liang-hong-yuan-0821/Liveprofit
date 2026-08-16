#!/usr/bin/env python
# YoHo - 多智能体股票交易分析系统
# 基于 TradingAgents-CN，简化为 OpenAI 兼容 API + Tushare 数据源

import os
from pathlib import Path
from dotenv import load_dotenv

from AI.graph.trading_graph import TradingAgentsGraph
from AI.default_config import load_config
from AI.utils.logging_init import init_logging
from AI.utils.call_trace import trace_call, trace_step


@trace_call(show_params=["debug", "config"])
def _create_graph(config):
    """创建 TradingAgentsGraph 实例"""
    return TradingAgentsGraph(selectedLayer=['market'], debug=True, config=config)


def _load_env_file():
    """加载 .env 文件（参考 TradingAgents-CN ConfigManager._load_env_file）
    override=False 确保系统环境变量优先级高于 .env 文件
    """
    project_root = Path(__file__).parent
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def main():
    """主入口：初始化并运行交易分析"""
    trace_step("启动 YoHo 系统")
    _load_env_file()
    config = load_config()
    init_logging(config.get("log_level", "INFO"))
    trace_step("配置加载完成", llm_model=config.get("quick_think_llm"),
               log_level=config.get("log_level"))

    ta = _create_graph(config)

    # 示例：分析平安银行
    trace_step("开始分析", company="000001.SZ", trade_date="2024-12-20")
    init_state = ta.propagator.create_initial_state("2024-12-20")
    init_state["company_of_interest"] = "000001.SZ"
    state, decision = ta.propagate(init_state)
    trace_step("分析完成", action=decision.get("action"),
               target_price=decision.get("target_price"),
               confidence=decision.get("confidence"))

    print("=" * 60)
    print("最终决策:")
    print(decision)
    print("=" * 60)

    # 可选：在知道实际收益后，进行反思和学习
    # ta.reflect_and_remember(500)


if __name__ == "__main__":
    main()

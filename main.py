#!/usr/bin/env python
# YoHo - 多智能体股票交易分析系统
# 基于 TradingAgents-CN，简化为 OpenAI 兼容 API + Tushare 数据源

from AI.graph.trading_graph import TradingAgentsGraph
from AI.default_config import load_config
from AI.utils.logging_init import init_logging


def main():
    """主入口：初始化并运行交易分析"""
    config = load_config()
    init_logging(config.get("log_level", "INFO"))

    ta = TradingAgentsGraph(debug=True, config=config)

    # 示例：分析平安银行
    state, decision = ta.propagate("000001.SZ", "2024-12-20")
    print("=" * 60)
    print("最终决策:")
    print(decision)
    print("=" * 60)

    # 可选：在知道实际收益后，进行反思和学习
    # ta.reflect_and_remember(500)


if __name__ == "__main__":
    main()

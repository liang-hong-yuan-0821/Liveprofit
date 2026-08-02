"""
YoHo 标的工具函数
确保 Agent 在整个分析流程中使用一致的股票代码。
"""


def build_instrument_context(ticker: str) -> str:
    """构建标的约束上下文，确保 Agent 使用精确的股票代码"""
    normalized_ticker = str(ticker).strip().upper()
    return (
        f"当前分析标的的精确股票代码是 `{normalized_ticker}`。"
        "在所有工具调用、分析报告、交易建议和最终结论中，都必须使用这个完全一致的股票代码。"
        "如果代码带有交易所后缀，必须原样保留，绝对不能省略、改写或替换。"
    )

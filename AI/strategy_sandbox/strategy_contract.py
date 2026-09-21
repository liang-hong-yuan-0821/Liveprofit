"""策略 Sandbox 的唯一字段与输出合同；不得导入 backend 业务模块。"""

from __future__ import annotations

ACTIONS = frozenset({"BUY", "SELL_ALL", "SELL_PARTIAL", "HOLD"})
RETURN_KEYS = (
    "action", "score", "entry_price", "stop_loss", "take_profit", "sell_ratio", "reason",
)
ALLOWED_CALLS = frozenset({"abs", "min", "max", "round", "isfinite"})
ALLOWED_ARRAY_INDEXES = frozenset(range(-250, 0))

OHLCV_PATHS = {
    ("ohlcv", name) for name in ("trade_date", "open", "high", "low", "close", "volume", "amount")
}
QFQ_INDICATOR_PATHS = {
    ("indicators", name) for name in (
        "ma_qfq_5", "ma_qfq_20", "ma_qfq_60",
        "boll_mid_qfq", "boll_upper_qfq", "boll_lower_qfq",
        "macd_dif_qfq", "macd_dea_qfq", "macd_qfq", "rsi_qfq_6",
    )
}
# 只为已有已发布版本保留；运行期投影的值仍来自 qfq，不再读取 bfq 数据列。
LEGACY_INDICATOR_PATHS = {
    ("indicators", "ma_bfq_5"), ("indicators", "ma_bfq_20"),
    ("indicators", "rsi_bfq_6"),
}
INDEXED_PATHS = frozenset(OHLCV_PATHS | QFQ_INDICATOR_PATHS | LEGACY_INDICATOR_PATHS)
SCALAR_PATHS = frozenset({
    ("meta", "symbol"), ("meta", "effective_trade_date"),
    ("meta", "requested_trade_date"), ("meta", "market_as_of_trade_date"),
    ("meta", "latest_bar_trade_date"), ("meta", "factor_trade_date"),
    ("meta", "bars_count"), ("meta", "price_basis"),
    ("meta", "signal_price_basis"), ("meta", "execution_price_basis"),
    ("meta", "adj_factor_version"), ("meta", "data_hash"),
    ("position", "shares"), ("position", "average_cost"), ("position", "market_value"),
})

"""LiveProfit 个股技术指标报告（不自算，2026-09-12 决策）。

指标值一律取自 Tushare stk_factor_pro 因子端点（技术指标数据源切换方案 §3.4），
本地不再实现任何指标算法（原 stockstats 自算链已删除，依赖已移除）。
"""

import logging
from datetime import datetime, timedelta

from pandas import isna

from AI.dataflows import interface

logger = logging.getLogger(__name__)

# 14 项报告指标：(stk_factor_pro 字段, 报告键)。全部为端点现成字段。
# 原 stockstats 15 项中 5 项无对应字段（方案 §3.4.1 映射表）：
# close_50_sma→ma_bfq_60、close_200_sma→ma_bfq_250、rsi_14→rsi_bfq_12、
# rsi_28→rsi_bfq_24、volume_delta 删除。
REPORT_INDICATORS = [
    ("ma_bfq_5", "close_5_sma"), ("ma_bfq_10", "close_10_sma"),
    ("ma_bfq_20", "close_20_sma"), ("ma_bfq_60", "close_60_sma"),
    ("ma_bfq_250", "close_250_sma"),
    ("rsi_bfq_6", "rsi_6"), ("rsi_bfq_12", "rsi_12"), ("rsi_bfq_24", "rsi_24"),
    ("macd_dif_bfq", "macd"), ("macd_dea_bfq", "macds"), ("macd_bfq", "macdh"),
    ("boll_mid_bfq", "boll"), ("boll_upper_bfq", "boll_ub"), ("boll_lower_bfq", "boll_lb"),
]

# 因子是快照值，无需长回看；30 天窗口覆盖长假停牌回退（取 <= curr_date 最新行）
_FACTOR_WINDOW_DAYS = 30


class StockstatsUtils:
    """个股技术指标报告工具（stk_factor_pro 因子映射，不自算）。"""

    @staticmethod
    def get_indicators_report(symbol: str, curr_date: str, lookback_days: int = 365) -> str:
        """生成综合技术指标报告（14 项 API 口径因子）。

        Args:
            symbol: 股票代码 (如 000001.SZ)
            curr_date: 当前日期 YYYY-mm-dd
            lookback_days: 仅保留签名兼容（LLM tool 参数），不参与取数——因子为
                快照值，取数窗口固定 30 天（_FACTOR_WINDOW_DAYS）。

        Returns:
            格式化的技术指标报告文本；数据源不支持/无数据时返回 "N/A: ..."。
        """
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_date = (curr_dt - timedelta(days=_FACTOR_WINDOW_DAYS)).strftime("%Y-%m-%d")

        df = interface.get_stock_factor_df(symbol, start_date, curr_date)
        if df is None:
            # 两种原因无法区分（接口层不暴露）：AKShare 不支持 / Tushare 上游取数失败
            return "N/A: 技术因子数据不可用（数据源不支持或上游取数失败）"
        if df.empty:
            return "N/A: 区间内无行情数据 (停牌/非交易日)"

        # provider 已按 trade_date 升序：取 <= curr_date 的最新一行（非交易日回退上一交易日）
        last = df.iloc[-1]
        actual_date = str(last["trade_date"])
        if actual_date != curr_date:
            logger.info(f"技术因子日期校正: {curr_date} → {actual_date}")

        results = []
        for field, display in REPORT_INDICATORS:
            val = last.get(field)
            results.append(f"  {display}: {'N/A' if val is None or isna(val) else val}")

        header = f"技术指标报告 - {symbol} @ {curr_date}\n" + "=" * 50
        return header + "\n" + "\n".join(results)

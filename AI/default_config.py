"""
YoHo 默认配置
从环境变量读取所有配置项，提供合理的默认值。
"""

import os


def load_config():
    """从环境变量加载配置，返回配置字典"""
    return {
        # LLM 配置 (OpenAI 兼容 API)
        "api_key": os.getenv("YOHO_API_KEY", ""),
        "base_url": os.getenv("YOHO_BASE_URL", "https://api.openai.com/v1"),
        "quick_think_llm": os.getenv("YOHO_QUICK_MODEL", "gpt-4o-mini"),
        "deep_think_llm": os.getenv("YOHO_DEEP_MODEL", "gpt-4o"),
        "quick_temperature": float(os.getenv("YOHO_QUICK_TEMPERATURE", "0.7")),
        "deep_temperature": float(os.getenv("YOHO_DEEP_TEMPERATURE", "0.3")),
        "max_tokens": int(os.getenv("YOHO_MAX_TOKENS", "8192")),

        # 数据源
        "data_source": os.getenv("YOHO_DATA_SOURCE", "tushare"),

        # 数据库
        "redis_enabled": os.getenv("REDIS_ENABLED", "false").lower() == "true",
        "redis_connection_string": os.getenv("REDIS_CONNECTION_STRING", ""),

        # 缓存
        "cache_strategy": os.getenv("TA_CACHE_STRATEGY", "file"),

        # 辩论和讨论
        "max_debate_rounds": int(os.getenv("YOHO_MAX_DEBATE_ROUNDS", "1")),
        "max_risk_discuss_rounds": int(os.getenv("YOHO_MAX_RISK_ROUNDS", "1")),
        "max_recur_limit": int(os.getenv("YOHO_RECURSION_LIMIT", "100")),

        # 记忆
        "memory_enabled": os.getenv("YOHO_MEMORY_ENABLED", "true").lower() == "true",
        "memory_path": os.getenv("YOHO_MEMORY_PATH", "./chroma_db"),

        # 日志
        "log_level": os.getenv("YOHO_LOG_LEVEL", "INFO"),

        # 选股层（全市场模式，selectedLayer 含 "screening"）
        "max_screened_stocks": int(os.getenv("YOHO_MAX_SCREENED_STOCKS", "10")),
        "screening_lookback_days": int(os.getenv("YOHO_SCREENING_LOOKBACK_DAYS", "10")),
        "screening_min_turnover": float(os.getenv("YOHO_SCREENING_MIN_TURNOVER", "50000000")),
        "max_constituents": int(os.getenv("YOHO_MAX_CONSTITUENTS", "50")),

        # 仓位管理层（全市场模式）
        "total_capital": float(os.getenv("YOHO_TOTAL_CAPITAL", "0")),
        "max_position_pct": float(os.getenv("YOHO_MAX_POSITION_PCT", "0.8")),
        "max_single_stock_pct": float(os.getenv("YOHO_MAX_SINGLE_STOCK_PCT", "0.1")),
        "max_sector_pct": float(os.getenv("YOHO_MAX_SECTOR_PCT", "0.3")),
        "position_sizing_strategy": os.getenv("YOHO_POSITION_SIZING_STRATEGY", "confidence_weighted"),

        # 交易日历校正
        "tushare_data_ready_hour": int(os.getenv("TUSHARE_DATA_READY_HOUR", "20")),
        "trade_calendar_refresh_days": int(os.getenv("TRADE_CALENDAR_REFRESH_DAYS", "365")),
        "trade_date_retry_steps": int(os.getenv("TRADE_DATE_RETRY_STEPS", "3")),
    }

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
        "mongodb_enabled": os.getenv("MONGODB_ENABLED", "false").lower() == "true",
        "redis_enabled": os.getenv("REDIS_ENABLED", "false").lower() == "true",
        "mongodb_connection_string": os.getenv("MONGODB_CONNECTION_STRING", ""),
        "mongodb_database": os.getenv("MONGODB_DATABASE", "yoho"),
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

        # 交易日历校正
        "tushare_data_ready_hour": int(os.getenv("TUSHARE_DATA_READY_HOUR", "20")),
        "trade_calendar_refresh_days": int(os.getenv("TRADE_CALENDAR_REFRESH_DAYS", "365")),
        "trade_date_retry_steps": int(os.getenv("TRADE_DATE_RETRY_STEPS", "3")),
    }

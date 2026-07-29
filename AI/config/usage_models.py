"""
YoHo 使用统计模型
参考 TradingAgents-CN 的数据模型定义。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class UsageRecord:
    """LLM API 调用使用记录"""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    provider: str = "openai"
    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    currency: str = "CNY"
    session_id: Optional[str] = None
    analysis_type: str = "stock_analysis"


@dataclass
class ModelConfig:
    """模型配置"""
    provider: str = "openai"
    model_name: str = ""
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 4000
    temperature: float = 0.7
    enabled: bool = True


@dataclass
class PricingConfig:
    """定价配置（每1K token）"""
    provider: str = ""
    model_name: str = ""
    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0
    currency: str = "CNY"

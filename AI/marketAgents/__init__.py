"""
市场层 (Market Layer) — 宏观市场分析独立子图

三层 Subgraph 架构的第一层：
- Layer 0: 国际新闻分析（跨国宏观事件 + 历史案例类比）
- Layer 1: 各国市场分析（US / KR / CN，每国 = 新闻 + 技术）

不依赖 ticker，仅使用 trade_date。
"""

from AI.marketAgents.analysts.international_news_analyst import create_international_news_analyst
from AI.marketAgents.analysts.us_news_analyst import create_us_news_analyst
from AI.marketAgents.analysts.us_tech_analyst import create_us_tech_analyst
from AI.marketAgents.analysts.kr_news_analyst import create_kr_news_analyst
from AI.marketAgents.analysts.kr_tech_analyst import create_kr_tech_analyst
from AI.marketAgents.analysts.cn_news_analyst import create_cn_news_analyst
from AI.marketAgents.analysts.cn_tech_analyst import create_cn_tech_analyst

__all__ = [
    "create_international_news_analyst",
    "create_us_news_analyst",
    "create_us_tech_analyst",
    "create_kr_news_analyst",
    "create_kr_tech_analyst",
    "create_cn_news_analyst",
    "create_cn_tech_analyst",
]

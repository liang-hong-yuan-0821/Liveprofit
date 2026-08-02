"""板块层 Analyst 工厂函数"""

from AI.sectorAgents.analysts.sector_news_analyst import create_sector_news_analyst
from AI.sectorAgents.analysts.sector_tech_analyst import create_sector_tech_analyst

__all__ = [
    "create_sector_news_analyst",
    "create_sector_tech_analyst",
]

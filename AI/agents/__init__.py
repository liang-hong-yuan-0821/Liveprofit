"""
YoHo Agents 包
导出所有 Agent 工厂函数和工具类。
"""

from AI.agents.utils.agent_utils import Toolkit, create_msg_delete
from AI.agents.utils.agent_states import AgentState, InvestDebateState, RiskDebateState
from AI.agents.utils.memory import FinancialSituationMemory

from AI.agents.analysts.market_analyst import create_market_analyst
from AI.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from AI.agents.analysts.news_analyst import create_news_analyst
from AI.agents.analysts.social_media_analyst import create_social_media_analyst
from AI.agents.analysts.tech_market_analyst import create_tech_market_analyst

from AI.agents.researchers.bull_researcher import create_bull_researcher
from AI.agents.researchers.bear_researcher import create_bear_researcher

from AI.agents.managers.research_manager import create_research_manager
from AI.agents.managers.risk_manager import create_risk_manager

from AI.agents.risk_mgmt.aggresive_debator import create_risky_debator
from AI.agents.risk_mgmt.conservative_debator import create_safe_debator
from AI.agents.risk_mgmt.neutral_debator import create_neutral_debator

from AI.agents.trader.trader import create_trader

__all__ = [
    # 工具类
    "Toolkit",
    "create_msg_delete",
    # 状态
    "AgentState",
    "InvestDebateState",
    "RiskDebateState",
    "FinancialSituationMemory",
    # 分析师
    "create_market_analyst",
    "create_fundamentals_analyst",
    "create_news_analyst",
    "create_social_media_analyst",
    "create_tech_market_analyst",
    # 研究员
    "create_bull_researcher",
    "create_bear_researcher",
    # 经理
    "create_research_manager",
    "create_risk_manager",
    # 风险管理
    "create_risky_debator",
    "create_safe_debator",
    "create_neutral_debator",
    # 交易员
    "create_trader",
]

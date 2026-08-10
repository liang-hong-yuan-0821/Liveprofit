"""
pytest 共享 fixtures —— 真实 LLM + 真实 Toolkit + 真实 Memory。

所有 agent 集成测试使用真实依赖，验证提示词 + tool 的实际效果。
LLM 和 Toolkit 按 module 级别复用，避免每个测试函数重复初始化。
"""

import pytest
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env（在 import 真实依赖之前）
_project_root = Path(__file__).resolve().parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)

from langchain_openai import ChatOpenAI
from AI.default_config import load_config
from AI.stockAgents import Toolkit
from AI.stockAgents.utils.memory import FinancialSituationMemory


# ============================================================
# Module 级别 fixtures —— 整个测试文件共享一份实例
# ============================================================

@pytest.fixture(scope="module")
def real_llm():
    """真实 ChatOpenAI LLM（quick_thinking，来自 .env 配置）"""
    config = load_config()
    if not config.get("api_key"):
        pytest.skip("未配置 YOHO_API_KEY，跳过集成测试")
    return ChatOpenAI(
        model=config["quick_think_llm"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        temperature=config["quick_temperature"],
        max_tokens=config["max_tokens"],
        timeout=180,
    )


@pytest.fixture(scope="module")
def real_toolkit():
    """真实 Toolkit（Tushare / AKShare，来自 .env 配置）"""
    config = load_config()
    return Toolkit(config=config)


@pytest.fixture(scope="module")
def real_memory():
    """真实 FinancialSituationMemory（ChromaDB）"""
    config = load_config()
    if not config.get("memory_enabled", True):
        return None
    return FinancialSituationMemory("test_memory", config)

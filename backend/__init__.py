"""Liveprofit 后端平台包。

模块化单体：backend.api 为唯一 HTTP/SSE 入站适配层，
backend.modules 为领域模块（analysis / market_data / event_study / investment_workspace），
backend.bootstrap 为组合根，backend.workers 为异步执行面入口。
AI/ 内核不反向导入 backend。
"""

__version__ = "0.1.0"

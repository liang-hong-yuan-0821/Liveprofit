"""modules：领域模块所有权边界（analysis / market_data / event_study / investment_workspace）。

每个模块内部按 application/domain/infrastructure 分层；仅 application 对外暴露
Command、Query、Result 和领域错误。跨模块只允许依赖目标模块 application/ 导出的 Contract。
"""

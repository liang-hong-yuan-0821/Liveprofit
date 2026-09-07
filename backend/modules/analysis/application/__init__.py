"""analysis 应用层：Task/Report/Event/Outbox 用例的 Command、Result 与领域错误。

仅本层对外暴露 Contract；不解析 HTTP、不依赖 Redis/Dramatiq 客户端类型。
"""

"""db.instrument — 证券市场数据库（market schema）中立包。

连接/建库/DAO/采集/迁移的唯一实现（证券市场数据库统一方案 3.1）；
provider 工厂注入保持零 AI/backend 依赖，backend 与 AI 均 import 本包。
"""

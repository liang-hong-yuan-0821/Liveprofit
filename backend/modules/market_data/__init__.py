"""market_data：面向市场门户的资产目录、指数 Bars、概念热点快照、数据新鲜度与采集投影。

不等同于所有 Provider 原始数据的总仓；只消费 DataFrame→显式 mapper→规范化表的
结构化数据，禁止解析 Markdown（§3.1.5 市场 Provider 能力评估）。
"""

from __future__ import annotations

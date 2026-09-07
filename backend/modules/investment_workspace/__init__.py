"""investment_workspace：自选分组/标的与手工组合/持仓（两个独立聚合）。

仅共享无持久化行为的 InstrumentRef 值对象；Repository 禁止跨聚合访问（T3+ 实现）。
"""

from __future__ import annotations

"""quant_strategy 领域值对象（plan 4.1.1）。"""

from __future__ import annotations

import enum


class StrategyStatus(str, enum.Enum):
    """策略版本状态：仅 DRAFT → PUBLISHED、PUBLISHED → ARCHIVED 有效。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"

"""共享 InstrumentRef 值对象（无持久化行为；两个聚合独立维护 revision）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

MARKETS = frozenset({"US", "KR", "CN"})
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


@dataclass(frozen=True)
class InstrumentRef:
    market: str  # US/KR/CN
    symbol: str


def is_valid_instrument(instrument: InstrumentRef) -> bool:
    return instrument.market in MARKETS and bool(SYMBOL_PATTERN.match(instrument.symbol))

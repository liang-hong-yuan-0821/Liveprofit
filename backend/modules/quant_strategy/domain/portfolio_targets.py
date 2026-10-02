"""Trusted portfolio target contract, separate from sandbox single-symbol signals."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class TargetLeg:
    ts_code: str
    family_weight: Decimal
    # Optional host-normalized raw execution-price bounds, inclusive.
    entry_lower: Decimal | None = None
    entry_upper: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.ts_code or not self.ts_code.endswith((".SH", ".SZ")):
            raise ValueError("target code must be exchange-qualified")
        if (not isinstance(self.family_weight, Decimal) or not self.family_weight.is_finite()
                or not 0 < self.family_weight <= 1):
            raise ValueError("target weight must be in (0, 1]")
        if (self.entry_lower is None) != (self.entry_upper is None):
            raise ValueError("entry interval requires both bounds")
        if (self.entry_lower is not None
                and (not isinstance(self.entry_lower, Decimal) or not isinstance(self.entry_upper, Decimal)
                     or not self.entry_lower.is_finite() or not self.entry_upper.is_finite()
                     or not 0 < self.entry_lower <= self.entry_upper)):
            raise ValueError("invalid entry interval")


@dataclass(frozen=True)
class PortfolioTargetIntent:
    family_id: str
    strategy_version_id: uuid.UUID
    evaluation_as_of: date
    decision_date: date
    valid_until: date
    policy_id: str
    reason: str
    legs: tuple[TargetLeg, ...]
    cash_only: bool = False
    trial_id: str | None = None
    definition_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.family_id or not self.policy_id or not self.reason:
            raise ValueError("portfolio target provenance is required")
        if not isinstance(self.strategy_version_id, uuid.UUID):
            raise TypeError("portfolio target requires frozen strategy version")
        if not all(isinstance(value, date) for value in (
            self.evaluation_as_of, self.decision_date, self.valid_until,
        )):
            raise TypeError("portfolio target dates must be dates")
        if not self.evaluation_as_of <= self.decision_date <= self.valid_until:
            raise ValueError("target evaluation and validity dates are inconsistent")
        if (not isinstance(self.legs, tuple)
                or not all(isinstance(leg, TargetLeg) for leg in self.legs)
                or len({leg.ts_code for leg in self.legs}) != len(self.legs)):
            raise ValueError("portfolio target requires unique valid legs")
        if not isinstance(self.cash_only, bool) or self.cash_only != (len(self.legs) == 0):
            raise ValueError("all-cash target must have no legs; invested target must have legs")
        if sum((leg.family_weight for leg in self.legs), Decimal(0)) > 1:
            raise ValueError("family weights must leave any unused budget in cash")
        if (self.trial_id is None) != (self.definition_hash is None):
            raise ValueError("trial identity and definition hash must travel together")
        if self.trial_id is not None and (
            not isinstance(self.trial_id, str) or not self.trial_id.strip()
            or not isinstance(self.definition_hash, str) or len(self.definition_hash) != 64
            or any(c not in "0123456789abcdef" for c in self.definition_hash)
        ):
            raise ValueError("invalid frozen trial identity")

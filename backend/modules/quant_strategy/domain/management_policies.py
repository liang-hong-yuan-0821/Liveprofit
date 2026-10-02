"""Frozen exit-policy definitions; a missing target price needs a real exit rule."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ExitPolicyKind(StrEnum):
    FIXED_TARGET = "FIXED_TARGET"
    TRAILING = "TRAILING"
    RULE_BASED = "RULE_BASED"


class StopMode(StrEnum):
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    FRACTION = "FRACTION"
    ATR_MULTIPLE = "ATR_MULTIPLE"
    VOLATILITY = "VOLATILITY"


@dataclass(frozen=True)
class InitialStopRule:
    mode: StopMode
    value: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, StopMode):
            raise TypeError("initial stop rule requires a typed mode")
        if self.mode == StopMode.ENTRY_SIGNAL:
            if self.value is not None:
                raise ValueError("entry signal stop must not contain a numeric override")
            return
        if not isinstance(self.value, Decimal):
            raise TypeError("numeric initial stop requires a Decimal value")
        if not self.value.is_finite() or self.value <= 0:
            raise ValueError("initial stop value must be positive and finite")
        if self.mode == StopMode.FRACTION and self.value >= 1:
            raise ValueError("fractional initial stop must be below 1")


@dataclass(frozen=True)
class ManagementPolicy:
    policy_id: str
    kind: ExitPolicyKind
    initial_stop: InitialStopRule
    fixed_target_r: Decimal | None = None
    trailing_atr_multiple: Decimal | None = None
    rule_exit_conditions: tuple[str, ...] = ()
    max_holding_sessions: int | None = None
    initial_exposure: Decimal = Decimal(1)
    add_at_r: Decimal | None = None
    max_adds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id or not isinstance(self.kind, ExitPolicyKind):
            raise ValueError("management policy identity and kind are required")
        if not isinstance(self.initial_stop, InitialStopRule):
            raise TypeError("management policy requires an initial stop")
        if not isinstance(self.initial_exposure, Decimal) or not self.initial_exposure.is_finite() or not 0 < self.initial_exposure <= 1:
            raise ValueError("initial exposure must be in (0,1]")
        if type(self.max_adds) is not int or self.max_adds < 0:
            raise ValueError("max_adds must be nonnegative")
        if self.add_at_r is not None and (not isinstance(self.add_at_r, Decimal)
                                          or not self.add_at_r.is_finite() or self.add_at_r <= 0):
            raise ValueError("add threshold must be positive finite R")
        if (self.max_adds > 0) != (self.add_at_r is not None):
            raise ValueError("add count and add-at-R threshold must be supplied together")
        if self.max_holding_sessions is not None and (
            type(self.max_holding_sessions) is not int or self.max_holding_sessions <= 0
        ):
            raise ValueError("max holding sessions must be positive")
        if not isinstance(self.rule_exit_conditions, tuple) or any(
            not isinstance(value, str) or not value for value in self.rule_exit_conditions
        ) or len(set(self.rule_exit_conditions)) != len(self.rule_exit_conditions):
            raise ValueError("exit conditions must be unique nonempty strings")

        fixed = self.fixed_target_r
        trailing = self.trailing_atr_multiple
        if self.kind == ExitPolicyKind.FIXED_TARGET:
            if (not isinstance(fixed, Decimal) or not fixed.is_finite() or fixed <= 0
                    or trailing is not None or self.rule_exit_conditions or self.max_holding_sessions is not None):
                raise ValueError("fixed target policy requires only a positive R target")
        elif self.kind == ExitPolicyKind.TRAILING:
            if (fixed is not None or not isinstance(trailing, Decimal)
                    or not trailing.is_finite() or trailing <= 0):
                raise ValueError("trailing policy requires a positive ATR trail and no fixed target")
        elif (self.kind == ExitPolicyKind.RULE_BASED
              and (fixed is not None or trailing is not None or not self.rule_exit_conditions)):
            raise ValueError("rule-based policy requires explicit exits and no target price")

    @property
    def permits_null_take_profit(self) -> bool:
        return self.kind in (ExitPolicyKind.TRAILING, ExitPolicyKind.RULE_BASED)

    def to_config(self) -> dict:
        """Persist only JSON primitives; Decimal values retain exact decimal text."""
        return {
            "policy_id": self.policy_id,
            "kind": self.kind.value,
            "initial_stop": {"mode": self.initial_stop.mode.value,
                             "value": str(self.initial_stop.value) if self.initial_stop.value is not None else None},
            "fixed_target_r": str(self.fixed_target_r) if self.fixed_target_r is not None else None,
            "trailing_atr_multiple": str(self.trailing_atr_multiple) if self.trailing_atr_multiple is not None else None,
            "rule_exit_conditions": list(self.rule_exit_conditions),
            "max_holding_sessions": self.max_holding_sessions,
            "initial_exposure": str(self.initial_exposure),
            "add_at_r": str(self.add_at_r) if self.add_at_r is not None else None,
            "max_adds": self.max_adds,
        }

    @classmethod
    def from_config(cls, config: dict) -> ManagementPolicy:
        """Fail closed on incomplete or unknown persisted policy fields."""
        fields = {
            "policy_id", "kind", "initial_stop", "fixed_target_r", "trailing_atr_multiple",
            "rule_exit_conditions", "max_holding_sessions", "initial_exposure", "add_at_r", "max_adds",
        }
        if not isinstance(config, dict) or set(config) != fields:
            raise ValueError("management policy config fields do not match the contract")
        stop = config["initial_stop"]
        if not isinstance(stop, dict) or set(stop) != {"mode", "value"}:
            raise ValueError("initial stop config fields do not match the contract")
        conditions = config["rule_exit_conditions"]
        if not isinstance(conditions, list):
            raise TypeError("exit conditions must be a JSON list")

        def decimal(value: object) -> Decimal:
            if not isinstance(value, str):
                raise TypeError("policy decimal must use an exact JSON string")
            try:
                return Decimal(value)
            except Exception as exc:
                raise ValueError("invalid policy decimal") from exc

        def optional_decimal(value: object) -> Decimal | None:
            return None if value is None else decimal(value)

        try:
            return cls(
                policy_id=config["policy_id"],
                kind=ExitPolicyKind(config["kind"]),
                initial_stop=InitialStopRule(StopMode(stop["mode"]), optional_decimal(stop["value"])),
                fixed_target_r=optional_decimal(config["fixed_target_r"]),
                trailing_atr_multiple=optional_decimal(config["trailing_atr_multiple"]),
                rule_exit_conditions=tuple(conditions),
                max_holding_sessions=config["max_holding_sessions"],
                initial_exposure=decimal(config["initial_exposure"]),
                add_at_r=optional_decimal(config["add_at_r"]),
                max_adds=config["max_adds"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid management policy config") from exc

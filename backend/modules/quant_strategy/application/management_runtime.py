"""Trusted host adaptation of frozen family policies; never grants risk admission."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy
from backend.modules.quant_strategy.domain.family_management import validate_family_policy


def resolve_management_snapshot(strategy: dict) -> ManagementPolicy | None:
    snapshot = strategy.get("lifecycle_policy")
    if not snapshot or "management_policy" not in snapshot.get("config", {}):
        return None
    config = snapshot["config"]
    if set(config) != {"management_policy"} or snapshot.get("required_fields") != []:
        raise ValueError("invalid frozen management policy envelope")
    policy = ManagementPolicy.from_config(config["management_policy"])
    validate_family_policy(policy, strategy.get("template_id"))
    canonical = json.dumps({"config": config, "required_fields": []},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if snapshot.get("content_hash") != digest or snapshot.get("policy_key") != policy.policy_id:
        raise ValueError("frozen management policy digest or identity mismatch")
    return policy


def adapt_management_output(output: dict, policy: ManagementPolicy, context: dict,
                            market: dict) -> dict:
    if output["action"] != "BUY":
        return output

    def positive(value):
        try:
            number = Decimal(str(value))
            return number if number.is_finite() and number > 0 else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    reason = None
    if policy.trailing_atr_multiple is not None:
        if positive(market.get("atr_qfq")) is None:
            reason = "冻结趋势政策缺少有效ATR"
    else:
        values = context.get("indicators", {}).get("ma_qfq_20", [])
        ma20 = positive(values[-1]) if values else None
        closes = context.get("ohlcv", {}).get("close", [])
        close = positive(closes[-1]) if closes else None
        entry = positive(output.get("entry_price"))
        if ma20 is None or close is None or entry is None:
            reason = "冻结MACD政策缺少有效MA20或价格"
        elif max(close, entry) >= ma20:
            reason = "入场已满足MA20退出条件"
    if reason:
        return {"action": "HOLD", "score": 0, "entry_price": None, "stop_loss": None,
                "take_profit": None, "sell_ratio": None, "reason": reason}
    return {**output, "take_profit": None}

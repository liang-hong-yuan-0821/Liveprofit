# test-catalog-begin
# {
#   "purpose": "量化策略 / history_row_image_types：Exact JSON types survive physical-original/interpretation verification.",
#   "keywords": [
#     "量化策略",
#     "历史审计",
#     "history_row_image_types",
#     "history"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/infrastructure/lifecycle_history_repository.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Exact JSON types survive physical-original/interpretation verification."""
import json

import pytest

from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    LifecycleHistoryIntegrityError,
    _verify_row_image,
)


@pytest.mark.parametrize("original,forged", [(1, True), (0, False), (True, 1), (False, 0)])
def test_integer_and_boolean_are_not_interchangeable(original, forged):
    raw = json.dumps({"nested": {"source_signal_id": original}})
    with pytest.raises(LifecycleHistoryIntegrityError, match="interpretation"):
        _verify_row_image({"nested": {"source_signal_id": forged}, "__raw_row_json__": raw})


def test_decimal_interpretation_preserves_exact_original_numeric_json():
    _verify_row_image({"amount": "9007199254740993.123456789", "nested": [True, "2.5"],
                       "__raw_row_json__": '{"amount":9007199254740993.123456789,"nested":[true,2.5]}'})

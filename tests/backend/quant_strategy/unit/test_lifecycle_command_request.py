# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_command_request（持仓生命周期、命令）：Request identity is original input, independent of mutable account state.",
#   "keywords": [
#     "量化策略",
#     "持仓生命周期",
#     "lifecycle_command_request",
#     "lifecycle"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_command_selection.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Request identity is original input, independent of mutable account state."""
import hashlib
import json
import uuid

import pytest

from backend.modules.quant_strategy.application.errors import FillValidationError
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)


@pytest.mark.parametrize("field,value", [
    ("order_id", "not-uuid"), ("status", []), ("status", "FILLED"),
    ("expected_revision", True), ("expected_revision", 0), ("expected_revision", "1"),
    ("request_key", " "), ("request_key", "x" * 129), ("request_key", None),
])
def test_invalid_original_requests_rejected(field, value):
    values = {"order_id": uuid.uuid4(), "status": "CANCELLED", "expected_revision": 1, "request_key": "test"}
    with pytest.raises(FillValidationError):
        OrderStatusCommandRequest(**(values | {field: value}))


def test_request_identity_covers_every_original_field_and_no_mutable_state():
    values = {"order_id": uuid.uuid4(), "status": "CANCELLED", "expected_revision": 1, "request_key": "保留原请求"}
    raw = OrderStatusCommandRequest(**values).canonical_request()
    assert raw == OrderStatusCommandRequest(**values).canonical_request()
    assert set(json.loads(raw)) == {"schema_version", "command_kind", "order_id", "status", "expected_revision", "request_key"}
    for field, value in (("order_id", uuid.uuid4()), ("status", "REJECTED"),
                         ("expected_revision", 2), ("request_key", "another")):
        changed = OrderStatusCommandRequest(**(values | {field: value})).canonical_request()
        assert hashlib.sha256(changed).digest() != hashlib.sha256(raw).digest()

"""新建必须携带非空源码；拒绝发生在任何数据库操作之前。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from backend.api.schemas.quant_strategies import QuantStrategyCreateRequest
from backend.modules.quant_strategy.application.errors import StrategyValidationFailedError
from backend.modules.quant_strategy.application.service import QuantStrategyService


@pytest.mark.parametrize("source", [None, "", " \n\t", "中" * 5000])
def test_create_request_rejects_missing_blank_or_oversized_source(source):
    data = {"name": "测试"}
    if source is not None:
        data["source_code"] = source
    with pytest.raises(ValidationError):
        QuantStrategyCreateRequest.model_validate(data)


@pytest.mark.parametrize("source", ["", " \n\t", "中" * 5000])
def test_service_rejects_invalid_source_before_database_access(source):
    session = Mock()
    uow = SimpleNamespace(session=session, commit=Mock())
    with pytest.raises(StrategyValidationFailedError):
        QuantStrategyService(uow).create("测试", source_code=source)
    assert not session.mock_calls
    uow.commit.assert_not_called()


def test_request_preserves_code_whitespace():
    source = "\ndef strategy(context):\n    return\n"
    assert QuantStrategyCreateRequest(name="测试", source_code=source).source_code == source

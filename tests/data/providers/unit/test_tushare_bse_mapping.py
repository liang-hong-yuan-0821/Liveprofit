# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_bse_mapping：The BSE identity map must be complete and one-to-one before use.",
#   "keywords": [
#     "数据源",
#     "tushare_bse_mapping"
#   ],
#   "covers": [
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""The BSE identity map must be complete and one-to-one before use."""

from unittest.mock import MagicMock

import pandas as pd

from AI.dataflows.providers.cn.tushare import TushareProvider


def _provider(frame):
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "Tushare"
    provider.connected = True
    provider.api = MagicMock()
    provider.api.bse_mapping.return_value = frame
    provider._api_call = lambda fn, timeout=None, **kwargs: fn(**kwargs)
    return provider


def _mapping():
    return pd.DataFrame({
        "o_code": [f"83{n:04d}.BJ" for n in range(200)],
        "n_code": [f"920{n:03d}.BJ" for n in range(200)],
        "list_date": ["20211115"] * 200,
    })


def test_bse_mapping_requires_complete_unique_codes():
    provider = _provider(_mapping())
    frame = provider.get_bse_mapping_df()
    assert len(frame) == 200
    assert provider.api.bse_mapping.call_args.kwargs["fields"] == (
        "o_code,n_code,list_date"
    )

    assert _provider(_mapping().head(199)).get_bse_mapping_df() is None
    duplicate = _mapping()
    duplicate.loc[1, "n_code"] = duplicate.loc[0, "n_code"]
    assert _provider(duplicate).get_bse_mapping_df() is None

"""
单元测试：TushareProvider 非 CN 指数日线（index_global 端点，US/KR 上线方案 3.1）

- GLOBAL_INDEX_CODE_MAP 分派：.INX/.DJI/.IXIC/KS11 → index_global（SPX/DJI/IXIC/KS11），
  CN 码不触发 index_global
- 10 列标准帧：升序归一、pre_close/change/pct_chg 直取上游原值（不自算）、
  amount 恒 NaN、swing 丢弃
- 入参日期归一：YYYYMMDD 与 YYYY-MM-DD 同结果
- 异常/空/未连接 → None（契约：失败不抛，触发采集链换兜底源）
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider


def _new_provider(api=None):
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    prov._api_call = lambda fn, *a, **kw: fn(*a, **kw)  # 直通（不经过线程池）
    return prov


def _global_frame() -> pd.DataFrame:
    """index_global 端点形态：降序 + 含 swing 列 + 无 amount 列（实测列集）。"""
    return pd.DataFrame({
        "ts_code": ["SPX", "SPX", "SPX"],
        "trade_date": ["20260911", "20260910", "20260909"],
        "open": [7636.75, 7594.74, 7580.06],
        "close": [7656.98, 7591.70, 7591.70],
        "high": [7677.02, 7612.86, 7601.10],
        "low": [7636.75, 7580.06, 7570.02],
        "pre_close": [7591.70, 7591.70, 7580.00],
        "change": [65.28, 0.00, 11.70],
        "pct_chg": [0.8599, 0.0, 0.1544],
        "swing": [0.53, 0.43, 0.41],
        "vol": [472248.0, 500000.0, 480000.0],
    })


# ==================== 分派 ====================

@pytest.mark.parametrize("platform_code,ts_code", [
    (".INX", "SPX"),
    (".DJI", "DJI"),
    (".IXIC", "IXIC"),
    ("KS11", "KS11"),
])
def test_non_cn_codes_dispatch_to_index_global(platform_code, ts_code):
    api = MagicMock()
    api.index_global.return_value = _global_frame().assign(ts_code=ts_code)
    prov = _new_provider(api)
    df = prov.get_index_data_df(platform_code, "20260901", "20260914")
    assert df is not None
    api.index_global.assert_called_once_with(
        ts_code=ts_code, start_date="20260901", end_date="20260914")


def test_cn_code_does_not_trigger_index_global():
    api = MagicMock()
    api.index_daily.return_value = pd.DataFrame({
        "ts_code": ["000001.SH"], "trade_date": ["20260911"],
        "open": [3000.0], "high": [3100.0], "low": [2900.0], "close": [3050.0],
        "pre_close": [3020.0], "change": [30.0], "pct_chg": [1.0],
        "vol": [100.0], "amount": [200.0],
    })
    prov = _new_provider(api)
    df = prov.get_index_data_df("000001.SH", "20260911", "20260911")
    assert df is not None
    assert df["close"].iloc[0] == 3050.0
    api.index_global.assert_not_called()


# ==================== 标准帧映射 ====================

def test_global_frame_maps_to_standard_10_cols_ascending():
    api = MagicMock()
    api.index_global.return_value = _global_frame()
    prov = _new_provider(api)
    df = prov.get_index_data_df(".INX", "20260901", "20260914")
    assert list(df.columns) == [
        "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]
    # 端点返回降序 → 升序归一
    assert df["trade_date"].tolist() == ["2026-09-09", "2026-09-10", "2026-09-11"]
    # pre_close/change/pct_chg 直取上游原值（不自算）：末行（最新日）为上游 20260911 值
    assert df["pre_close"].iloc[-1] == 7591.70
    assert df["change"].iloc[-1] == 65.28
    assert df["pct_chg"].iloc[-1] == 0.8599
    # amount 上游无此列 → 恒 NaN；swing 丢弃
    assert df["amount"].isna().all()
    assert "swing" not in df.columns


# ==================== 日期归一 ====================

def test_date_bounds_normalized_both_formats():
    for start, end in [("20260901", "20260914"), ("2026-09-01", "2026-09-14")]:
        api = MagicMock()
        api.index_global.return_value = _global_frame()
        prov = _new_provider(api)
        prov.get_index_data_df(".INX", start, end)
        _, kwargs = api.index_global.call_args
        assert kwargs["start_date"] == "20260901"
        assert kwargs["end_date"] == "20260914"


# ==================== 失败路径 ====================

def test_upstream_exception_returns_none():
    api = MagicMock()
    api.index_global.side_effect = RuntimeError("网络异常")
    prov = _new_provider(api)
    assert prov.get_index_data_df(".INX", "20260901", "20260914") is None


def test_empty_frame_returns_none():
    api = MagicMock()
    api.index_global.return_value = pd.DataFrame()
    prov = _new_provider(api)
    assert prov.get_index_data_df(".INX", "20260901", "20260914") is None


def test_disconnected_returns_none():
    prov = _new_provider()
    prov.connected = False
    assert prov.get_index_data_df(".INX", "20260901", "20260914") is None

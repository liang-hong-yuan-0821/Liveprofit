"""单元测试：TushareProvider 技术因子结构化接口（技术指标数据源切换方案 §3.1）

- get_index_factor_df（idx_factor_pro）：5 年分段分页拼接、无重复 trade_date、升序输出、
  某段失败/空段降级继续、全部失败 → None、未连接/日期格式错误 → None、标准化列与 fields 透传
- get_stock_factor_df（stk_factor_pro）：单次调用、升序归一、失败/空/未连接 → None
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import (
    INDEX_FACTOR_FIELDS,
    STOCK_FACTOR_FIELDS,
    TushareProvider,
)


def _new_provider(api=None):
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    prov._api_call = lambda fn, *a, **kw: fn(*a, **kw)  # 直通（不经过线程池）
    return prov


def _factor_frame(dates: list[str], ts_code: str = "000001.SH") -> pd.DataFrame:
    """构造降序 trade_date 的因子 DataFrame（模拟端点真实返回：新→旧）。"""
    n = len(dates)
    return pd.DataFrame({
        "ts_code": [ts_code] * n,
        "trade_date": sorted(dates, reverse=True),
        "close": [3000.0 + i for i in range(n)],
        "ma_bfq_5": [2990.0 + i for i in range(n)],
        "boll_mid_bfq": [3005.0 + i for i in range(n)],
        "macd_dif_bfq": [1.0 + i for i in range(n)],
    })


# ==================== get_index_factor_df ====================

def test_index_factor_paginates_5year_chunks_and_dedupes():
    # 2000-01-01 ~ 2005-01-03 跨 2 个 5 年段：第一段 20000101~20041230，第二段 20041231~20050103
    api = MagicMock()
    api.idx_factor_pro.side_effect = [
        _factor_frame(["20041230", "20000105"]),  # 段 1（降序）
        _factor_frame(["20050103", "20041231"]),  # 段 2（降序，跨段边界日不重复）
    ]
    prov = _new_provider(api)
    df = prov.get_index_factor_df("000001.SH", "2000-01-01", "2005-01-03")

    assert api.idx_factor_pro.call_count == 2
    # 两次调用参数：日期 YYYYMMDD、fields 默认 INDEX_FACTOR_FIELDS
    first_kwargs = api.idx_factor_pro.call_args_list[0].kwargs
    assert first_kwargs["ts_code"] == "000001.SH"
    assert first_kwargs["start_date"] == "20000101"
    assert first_kwargs["end_date"] == "20041230"
    assert first_kwargs["fields"] == INDEX_FACTOR_FIELDS

    # 拼接：4 行无重复、升序
    assert list(df["trade_date"]) == ["2000-01-05", "2004-12-30", "2004-12-31", "2005-01-03"]
    # 标准化：trade_date 转 YYYY-MM-DD str，数值列 to_numeric，无 ts_code 列
    assert "ts_code" not in df.columns
    assert df["ma_bfq_5"].dtype.kind == "f"


def test_index_factor_skips_failed_chunk_and_continues():
    api = MagicMock()
    api.idx_factor_pro.side_effect = [
        RuntimeError("upstream boom"),
        _factor_frame(["20050103"]),
    ]
    prov = _new_provider(api)
    df = prov.get_index_factor_df("000001.SH", "2000-01-01", "2005-01-03")
    # 第一段失败不阻断，第二段数据仍返回；缺段事实透传给消费方
    assert list(df["trade_date"]) == ["2005-01-03"]
    assert df.attrs["missing_chunks"] == 1


def test_index_factor_attrs_missing_chunks_zero_on_full_success():
    api = MagicMock()
    api.idx_factor_pro.side_effect = [_factor_frame(["20050103"])]
    prov = _new_provider(api)
    df = prov.get_index_factor_df("000001.SH", "2004-01-01", "2005-01-03")
    assert df.attrs["missing_chunks"] == 0


def test_index_factor_skips_empty_chunk():
    api = MagicMock()
    empty = pd.DataFrame()
    api.idx_factor_pro.side_effect = [
        empty,
        _factor_frame(["20050103"]),
    ]
    prov = _new_provider(api)
    df = prov.get_index_factor_df("000001.SH", "2000-01-01", "2005-01-03")
    assert list(df["trade_date"]) == ["2005-01-03"]
    # 空段窗口早于数据跨度（2005-01-03）→ 基日前合法空段，不计缺段
    assert df.attrs["missing_chunks"] == 0


def test_index_factor_hole_within_data_span_counts_as_missing():
    """数据跨度内的空段 = 真洞 → 计入缺段（基日后中间段空洞）。"""
    api = MagicMock()
    api.idx_factor_pro.side_effect = [
        _factor_frame(["20050105"]),  # 段 1（2005-01-01~2009-12-30）有数据
        pd.DataFrame(),  # 段 2（2009-12-31~2014-12-29）空：窗口与数据跨度重叠 → 真洞
        _factor_frame(["20150102"]),  # 段 3（2014-12-30~2015-01-03）有数据
    ]
    prov = _new_provider(api)
    df = prov.get_index_factor_df("000001.SH", "2005-01-01", "2015-01-03")
    assert df.attrs["missing_chunks"] == 1


def test_index_factor_all_failures_return_none():
    api = MagicMock()
    api.idx_factor_pro.side_effect = [RuntimeError("boom"), RuntimeError("boom again")]
    prov = _new_provider(api)
    assert prov.get_index_factor_df("000001.SH", "2000-01-01", "2005-01-03") is None


def test_index_factor_not_connected_and_bad_date_return_none():
    prov = _new_provider()
    prov.connected = False
    assert prov.get_index_factor_df("000001.SH", "2026-01-01", "2026-02-01") is None
    prov.connected = True
    assert prov.get_index_factor_df("000001.SH", "bad-date", "2026-02-01") is None


def test_index_factor_custom_fields_passthrough():
    api = MagicMock()
    api.idx_factor_pro.return_value = _factor_frame(["20260911"])
    prov = _new_provider(api)
    prov.get_index_factor_df("399001.SZ", "2026-09-01", "2026-09-12", fields="ts_code,trade_date,kdj_qfq")
    _, kwargs = api.idx_factor_pro.call_args
    assert kwargs["fields"] == "ts_code,trade_date,kdj_qfq"
    assert kwargs["ts_code"] == "399001.SZ"


# ==================== get_stock_factor_df ====================

def test_stock_factor_single_call_ascending_normalized():
    api = MagicMock()
    api.stk_factor_pro.return_value = pd.DataFrame({
        "ts_code": ["600000.SH"] * 3,
        "trade_date": ["20260911", "20260910", "20260909"],  # 降序（真实返回序）
        "ma_bfq_5": [9.27, 9.30, 9.23],
        "rsi_bfq_6": [50.2, 58.9, 60.1],
    })
    prov = _new_provider(api)
    df = prov.get_stock_factor_df("600000", "2026-09-01", "2026-09-12")

    api.stk_factor_pro.assert_called_once()
    _, kwargs = api.stk_factor_pro.call_args
    assert kwargs["ts_code"] == "600000.SH"  # 6 位裸代码归一
    assert kwargs["start_date"] == "20260901"
    assert kwargs["end_date"] == "20260912"
    assert kwargs["fields"] == STOCK_FACTOR_FIELDS

    assert list(df["trade_date"]) == ["2026-09-09", "2026-09-10", "2026-09-11"]
    assert "ts_code" not in df.columns
    assert df["rsi_bfq_6"].iloc[0] == 60.1


def test_stock_factor_empty_exception_and_not_connected_return_none():
    api = MagicMock()
    api.stk_factor_pro.return_value = pd.DataFrame()
    prov = _new_provider(api)
    assert prov.get_stock_factor_df("600000.SH", "2026-09-01", "2026-09-12") is None

    api.stk_factor_pro.side_effect = RuntimeError("boom")
    assert prov.get_stock_factor_df("600000.SH", "2026-09-01", "2026-09-12") is None

    api.stk_factor_pro.return_value = None
    assert prov.get_stock_factor_df("600000.SH", "2026-09-01", "2026-09-12") is None

    prov.connected = False
    assert prov.get_stock_factor_df("600000.SH", "2026-09-01", "2026-09-12") is None

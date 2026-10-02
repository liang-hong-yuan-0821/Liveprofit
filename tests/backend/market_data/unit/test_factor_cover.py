# test-catalog-begin
# {
#   "purpose": "个股 K 线因子缓存按实际 bars 日期精确覆盖判定。",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "K线",
#     "复权因子",
#     "factor_cover",
#     "bars",
#     "factor"
#   ],
#   "covers": [
#     "backend/modules/market_data/application/service.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""个股 K 线因子缓存按实际 bars 日期精确覆盖判定。"""

import pandas as pd

from backend.modules.market_data.application.service import (
    KLINE_FACTOR_COLUMNS,
    _factor_rows_cover,
)


def _frame(dates: list[str]) -> pd.DataFrame:
    data = {"trade_date": pd.to_datetime(dates)}
    data.update({column: [1.0] * len(dates) for column in KLINE_FACTOR_COLUMNS})
    return pd.DataFrame(data)


def test_no_expected_bars_is_covered_without_factor_rows():
    assert _factor_rows_cover(pd.DataFrame(), set()) is True


def test_empty_factor_frame_does_not_cover_expected_bars():
    assert _factor_rows_cover(pd.DataFrame(), {"2026-09-16"}) is False


def test_exact_bar_dates_are_covered():
    fdf = _frame(["2026-09-14", "2026-09-15", "2026-09-16"])
    assert _factor_rows_cover(
        fdf, {"2026-09-14", "2026-09-15", "2026-09-16"}
    ) is True


def test_internal_hole_is_not_covered_even_when_endpoints_exist():
    fdf = _frame(["2026-09-14", "2026-09-16"])
    assert _factor_rows_cover(
        fdf, {"2026-09-14", "2026-09-15", "2026-09-16"}
    ) is False


def test_extra_factor_dates_do_not_hurt_requested_window():
    fdf = _frame(["2026-09-12", "2026-09-14", "2026-09-15"])
    assert _factor_rows_cover(fdf, {"2026-09-14", "2026-09-15"}) is True


def test_rows_with_only_qfq_values_do_not_cover_kline_indicators():
    fdf = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-09-14"]),
        "ma_qfq_5": [10.0],
        **{column: [None] for column in KLINE_FACTOR_COLUMNS},
    })
    assert _factor_rows_cover(fdf, {"2026-09-14"}) is False

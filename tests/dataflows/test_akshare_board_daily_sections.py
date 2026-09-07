"""
单元测试：行业/概念接口的「近 10 个交易日逐日涨跌幅」章节（AKShare）

mock 模块级 ak 对象（SimpleNamespace + MagicMock），验证：
- 行业逐日章节格式（日期列 datetime / str 两种格式归一化 MM-DD、累计列）
- 概念逐日章节行序 = 热度排序 TOP30
- 部分板块日期覆盖不足 → 「—」
- 全市场缺某工作日 → 脚注列出该日、周末不在脚注
  （与 Tushare 概念路径 skipped 用同一日期布局互验口径一致）
"""

import types
from datetime import datetime as _real_dt
from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn import akshare


# 2026-08-19（周三）为"今天"；TRADING_11 = 最近 11 个交易日（升序）
DAY_TODAY = "20260819"
TRADING_11 = ["20260805", "20260806", "20260807", "20260810", "20260811",
              "20260812", "20260813", "20260814", "20260817", "20260818",
              "20260819"]
TRADING_10 = TRADING_11[1:]


def _geo_closes(base: float, rate: float, n: int) -> list:
    return [base * (1 + rate) ** i for i in range(n)]


def _em_hist(dates, closes, date_as_str=False, vol=None):
    """构造 EM 风格日线 DataFrame（列名 日期/收盘/成交量）"""
    if date_as_str:
        date_vals = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
    else:
        date_vals = pd.to_datetime(dates, format="%Y%m%d")
    cols = {"日期": date_vals, "收盘": closes}
    if vol is not None:
        cols["成交量"] = vol
    return pd.DataFrame(cols)


@pytest.fixture
def fake_ak(monkeypatch):
    """模块级 ak 替换：行业/概念名单 + 按 symbol 返回日线的 hist_em"""
    ak = types.SimpleNamespace()
    ak.stock_board_industry_name_em = MagicMock(
        return_value=pd.DataFrame({"板块名称": ["银行", "电子"]}))
    ak.stock_board_industry_hist_em = MagicMock()
    ak.stock_board_concept_name_em = MagicMock(
        return_value=pd.DataFrame({"概念名称": ["A概念", "B概念"]}))
    ak.stock_board_concept_hist_em = MagicMock()
    monkeypatch.setattr(akshare, "ak", ak)
    monkeypatch.setattr(akshare, "AKSHARE_AVAILABLE", True)
    return ak


def _setup_industry_hist(fake_ak, mapping):
    def fake(symbol=None, **kwargs):
        return mapping.get(symbol, pd.DataFrame())
    fake_ak.stock_board_industry_hist_em.side_effect = fake


def _setup_concept_hist(fake_ak, mapping):
    def fake(symbol=None, **kwargs):
        return mapping.get(symbol, pd.DataFrame())
    fake_ak.stock_board_concept_hist_em.side_effect = fake


def test_ak_industry_daily_section_format(fake_ak):
    """行业逐日章节：datetime/str 两种日期格式归一化 MM-DD、单元格与累计列正确"""
    _setup_industry_hist(fake_ak, {
        "银行": _em_hist(TRADING_11, _geo_closes(100, 0.02, 11)),            # datetime 日期
        "电子": _em_hist(TRADING_11, _geo_closes(100, -0.02, 11), True),     # str 日期
    })

    prov = akshare.AKShareProvider.__new__(akshare.AKShareProvider)
    out = prov.get_industry_sector_performance(days=10)

    # 标题下方展示聚合区间 YYYYMMDD - YYYYMMDD
    assert "# 全行业板块涨跌排名（近 10 日）\n（20260805 - 20260819）" in out
    assert "## 近10个交易日逐日涨跌幅（行业×日期，列头 MM-DD）" in out
    assert "| 行业 | 08-06 | 08-07 | 08-10 | 08-11 | 08-12 | 08-13 | 08-14 | 08-17 | 08-18 | 08-19 | 近10日累计 |" in out
    assert "| 银行 | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +21.90% |" in out
    assert "| 电子 | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -18.29% |" in out


def test_ak_concept_heat_daily_section_top30(fake_ak):
    """概念逐日章节：行序 = 热度排序（A>B），单元格来自 close 序列"""
    _setup_concept_hist(fake_ak, {
        "A概念": _em_hist(TRADING_11, _geo_closes(100, 0.02, 11),
                          vol=[1000.0] * 11),
        "B概念": _em_hist(TRADING_11, _geo_closes(100, 0.01, 11),
                          vol=[1000.0] * 11),
    })

    prov = akshare.AKShareProvider.__new__(akshare.AKShareProvider)
    out = prov.get_concept_board_heat_rank(days=10)

    # 热度 = pct*0.6（量恒定无量变）：A(21.90) > B(10.46)
    assert out.index("| A概念 | +2.0%") < out.index("| B概念 | +1.0%")
    assert "## 近10个交易日逐日涨跌幅（概念×日期，列头 MM-DD）" in out
    assert "| A概念 | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +21.90% |" in out
    assert "| B概念 | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +10.46% |" in out


def test_ak_missing_day_cell(fake_ak):
    """部分板块日期覆盖不足 → 缺失单元格「—」"""
    _setup_industry_hist(fake_ak, {
        "银行": _em_hist(TRADING_11, _geo_closes(100, 0.02, 11)),
        "电子": _em_hist(TRADING_11[-5:], _geo_closes(100, -0.02, 5)),   # 仅 5 行
    })

    prov = akshare.AKShareProvider.__new__(akshare.AKShareProvider)
    out = prov.get_industry_sector_performance(days=10)

    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    electronic_row = daily.split("| 电子 |", 1)[1].split("\n", 1)[0]
    assert electronic_row.count("—") >= 5


def test_ak_gap_footnote(fake_ak):
    """全市场缺某工作日（如节假日）→ 脚注列出该日、周末不在脚注
    （与 Tushare 概念路径 skipped 用同一日期布局：08-13 缺失、08-15/16 周末）"""
    dates = [d for d in TRADING_11 if d != "20260813"]
    _setup_industry_hist(fake_ak, {
        "银行": _em_hist(dates, _geo_closes(100, 0.02, len(dates))),
        "电子": _em_hist(dates, _geo_closes(100, -0.02, len(dates))),
    })

    prov = akshare.AKShareProvider.__new__(akshare.AKShareProvider)
    out = prov.get_industry_sector_performance(days=10)

    assert "> 数据缺口：以下日期无数据已跳过：" in out
    footnote = out.split("> 数据缺口", 1)[1]
    assert "08-13" in footnote
    assert "08-15" not in footnote and "08-16" not in footnote


class _FakeDateTime:
    """替换 akshare.datetime：now() 返回固定时刻（测试可控）"""
    _now = _real_dt(2026, 8, 19, 16, 0, 0)

    @classmethod
    def now(cls):
        return cls._now


def test_ak_industry_intraday_annotation(fake_ak, monkeypatch):
    """盘中运行且最新日期 == 今日 → 列头加（盘中）；盘后不加"""
    _setup_industry_hist(fake_ak, {
        "银行": _em_hist(TRADING_11, _geo_closes(100, 0.02, 11)),
    })
    _FakeDateTime._now = _real_dt(2026, 8, 19, 10, 0, 0)   # 盘中
    monkeypatch.setattr(akshare, "datetime", _FakeDateTime)

    prov = akshare.AKShareProvider.__new__(akshare.AKShareProvider)
    out = prov.get_industry_sector_performance(days=10)
    assert "| 08-19（盘中） |" in out

    _FakeDateTime._now = _real_dt(2026, 8, 19, 16, 0, 0)   # 盘后
    out = prov.get_industry_sector_performance(days=10)
    assert "（盘中）" not in out


def test_ak_nan_close_cell(fake_ak):
    """当日收盘 NaN → 单元格「—」而非 +nan%"""
    closes = _geo_closes(100, 0.02, 11)
    closes[2] = float("nan")
    _setup_industry_hist(fake_ak, {
        "银行": _em_hist(TRADING_11, closes),
        "电子": _em_hist(TRADING_11, _geo_closes(100, -0.02, 11)),
    })

    prov = akshare.AKShareProvider.__new__(akshare.AKShareProvider)
    out = prov.get_industry_sector_performance(days=10)

    assert "+nan%" not in out
    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    bank_row = daily.split("| 银行 |", 1)[1].split("\n", 1)[0]
    assert bank_row.count("—") >= 2

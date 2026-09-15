"""
单元测试：市场特征层（T5 验收）

覆盖验收标准：
- 4/19/59/119/249 边界样本（窗口 N 需 N 个样本；不足 → `value=None`）
- 未来数据丢弃并记录 `future_data_detected`
- 样本不足 → None；交易日口径窗口
- `market_data_quality` 无 reducer 冲突（单点写入：仅图启动 propagation.py）
- 阈值常量集中在 `market_features.py` 顶部并被单测覆盖
- 第十二章门控有序规则表、结构化解析降级、技术隔离（技术证据不含事件/新闻内容）

全部用例不触网：interface 层 8 个结构化接口在用例内 monkeypatch 为合成 payload。
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from AI.dataflows import interface as iface
from AI.dataflows import market_features as mf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = "2026-09-11"
AS_OF = "2026-09-10"

STRUCTURED_INTERFACES = (
    "get_market_index_features",
    "get_market_breadth_history",
    "get_market_fund_flow_history",
    "get_margin_trading_history",
    "get_market_valuation",
    "get_cn_liquidity_indicators",
    "get_cn_event_calendar",
    "get_global_risk_indicators",
)

DQ_OK = {"degradation_level": "ok", "actual_dates": {}, "missing_inputs": [],
         "future_data_detected": False, "notes": []}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """默认屏蔽 8 个结构化接口（用例内按需覆盖），保证测试零网络。"""
    for name in STRUCTURED_INTERFACES:
        monkeypatch.setattr(iface, name, lambda *a, **k: None)


# ==================== 合成数据 ====================

def _trading_dates(n, end=AS_OF):
    """n 个连续交易日（跳过周末；样本数断言用，不依赖官方日历）。"""
    cursor = date.fromisoformat(end)
    out = []
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return sorted(out)


def _closes(n, start=100.0, step=1.0):
    return [round(start + step * i, 4) for i in range(n)]


def _index_payload(closes_by_code, with_amount=True):
    indices = {}
    for code, closes in closes_by_code.items():
        dates = _trading_dates(len(closes))
        entry = {"trade_dates": dates, "close": closes}
        if with_amount:
            entry["amount"] = [1.0e6 + 1000.0 * i for i in range(len(closes))]
        indices[code] = entry
    return {"as_of_date": AS_OF, "indices": indices, "missing": {}, "notes": []}


def _breadth_payload(n=25, end=AS_OF):
    return {
        "as_of_date": end,
        "series": [
            {"trade_date": d, "up_ratio": 0.5, "limit_up": 60, "limit_down": 5,
             "broken_board_rate": 0.2, "max_board": 5, "promotion_rate": 0.3,
             "premium_rate": 0.02, "market_amount": 8.0e5}
            for d in _trading_dates(n, end)
        ],
        "missing": {},
        "notes": [],
    }


def _tech_features(monkeypatch, closes_by_code=None, with_amount=True):
    monkeypatch.setattr(
        iface, "get_market_index_features",
        lambda *a, **k: _index_payload(closes_by_code or {"000001.SH": _closes(30)},
                                       with_amount=with_amount))
    return mf.build_cn_technical_features(ANALYSIS)


def _quality_snapshot(core_status="ok", core_actual=AS_OF, measured=False):
    datasets = {}
    for name, meta in mf.MARKET_DATASETS.items():
        core = bool(meta.get("core"))
        status = core_status if core else "ok"
        datasets[name] = {
            "status": status,
            "actual_date": core_actual if status == "ok" else None,
            "measured": measured,
            "core": core,
            "note": "",
        }
    return mf.build_data_quality_summary(ANALYSIS, datasets)


def _gra(systemic="low", confidence="medium", dq=None):
    out = {"systemic_risk": systemic, "confidence": confidence}
    if dq is not None:
        out["data_quality"] = dq
    return out


def _regime(short="适合", wave="进攻", long_term="配置窗口", dq=None):
    out = {
        "short_term": {"level": short},
        "wave": {"level": wave},
        "long_term": {"level": long_term},
    }
    if dq is not None:
        out["data_quality"] = dq
    return out


# ==================== 1. 窗口边界样本（4/19/59/119/249） ====================

@pytest.mark.parametrize("samples,window", [(4, 5), (19, 20), (59, 60),
                                           (119, 120), (249, 250)])
def test_window_insufficient_at_boundary_minus_one(monkeypatch, samples, window):
    """N-1 个样本 → 窗口 N 指标为 None（sufficient=False）。"""
    feats = _tech_features(monkeypatch, {"000001.SH": _closes(samples)})
    ma = feats["derived_metrics"]["indices"]["000001.SH"]["ma"][window]
    assert ma["value"] is None
    assert ma["sufficient"] is False
    assert ma["window"] == window
    assert ma["samples"] == samples  # 全量样本数（N-1）


@pytest.mark.parametrize("samples,window", [(5, 5), (20, 20), (60, 60),
                                           (120, 120), (250, 250)])
def test_window_available_at_exact_sample_count(monkeypatch, samples, window):
    """恰好 N 个样本 → 窗口 N 指标可用（边界闭合）。"""
    feats = _tech_features(monkeypatch, {"000001.SH": _closes(samples)})
    entry = feats["derived_metrics"]["indices"]["000001.SH"]
    assert entry["ma"][window]["value"] is not None
    assert entry["ma"][window]["sufficient"] is True
    assert entry["return"][window]["value"] is not None
    assert entry["slope"][window]["value"] is not None


def test_window_metrics_numeric_semantics(monkeypatch):
    """MA = 尾部 N 均值；收益 = close[-1]/close[-N]-1（%）。"""
    closes = _closes(25)  # 100.0 .. 124.0
    feats = _tech_features(monkeypatch, {"000001.SH": closes})
    entry = feats["derived_metrics"]["indices"]["000001.SH"]
    assert entry["ma"][5]["value"] == pytest.approx(sum(closes[-5:]) / 5, abs=1e-4)
    expected_return = (closes[-1] / closes[-5] - 1.0) * 100.0
    assert entry["return"][5]["value"] == pytest.approx(expected_return, abs=1e-4)
    # 等比上升序列的归一化 OLS 斜率 = 1/均值×100
    expected_slope = 1.0 / (sum(closes[-5:]) / 5) * 100.0
    assert entry["slope"][5]["value"] == pytest.approx(expected_slope, abs=1e-3)


def test_sample_counts_are_trading_day_based(monkeypatch):
    """样本数按交易日计数（周末不计入），窗口为交易日口径。"""
    feats = _tech_features(monkeypatch, {"000001.SH": _closes(30)})
    entry = feats["derived_metrics"]["indices"]["000001.SH"]
    assert entry["samples"] == 30
    assert entry["ma"][20]["samples"] == 20
    # 日历日跨度 > 30（含周末），但交易日样本 = 30
    span = (date.fromisoformat(entry["as_of_date"])
            - date.fromisoformat(_trading_dates(30)[0])).days
    assert span >= 30


def test_trading_days_between_skips_weekend():
    """交易日计数：周五 → 下周一 = 1 个交易日；周五 → 周日 = 0。"""
    assert mf._trading_days_between("2026-09-11", "2026-09-14") == 1
    assert mf._trading_days_between("2026-09-11", "2026-09-13") == 0


def test_nth_trading_day_after_skips_holiday():
    """`_nth_trading_day_after` 跳过周末与法定长假（国庆）。"""
    assert mf._nth_trading_day_after("2026-09-11", 1) == "2026-09-14"
    assert mf._nth_trading_day_after("2026-09-11", 5) == "2026-09-18"
    # 2026-10-01 为国庆长假，不入交易日计数
    nth20 = mf._nth_trading_day_after("2026-09-11", 20)
    assert nth20 == "2026-10-19"


# ==================== 2. 未来数据丢弃 + future_data_detected ====================

def test_future_index_rows_discarded_and_flagged(monkeypatch):
    closes = _closes(20)
    payload = _index_payload({"000001.SH": closes})
    series = payload["indices"]["000001.SH"]
    series["trade_dates"] = series["trade_dates"] + ["2026-09-14"]
    series["close"] = closes + [1000.0]
    series["amount"] = series["amount"] + [9.9e6]
    monkeypatch.setattr(iface, "get_market_index_features", lambda *a, **k: payload)

    feats = mf.build_cn_technical_features(ANALYSIS)
    entry = feats["derived_metrics"]["indices"]["000001.SH"]
    assert feats["future_data_detected"] is True
    assert feats["data_quality"]["future_data_detected"] is True
    assert entry["as_of_date"] == AS_OF
    assert entry["close"]["value"] == pytest.approx(closes[-1])
    assert any("晚于" in note for note in feats["notes"])


def test_future_only_index_input_treated_as_missing(monkeypatch):
    """全部数据晚于分析日 → 该输入记为缺失（不允许静默空跑）。"""
    payload = {"as_of_date": "2026-09-14", "indices": {"000001.SH": {
        "trade_dates": ["2026-09-14", "2026-09-15"], "close": [1.0, 2.0]}}}
    monkeypatch.setattr(iface, "get_market_index_features", lambda *a, **k: payload)
    feats = mf.build_cn_technical_features(ANALYSIS)
    assert feats["future_data_detected"] is True
    assert "market_index_features" in feats["missing_inputs"]
    assert feats["derived_metrics"]["indices"] == {}


def test_future_breadth_fund_margin_rows_discarded(monkeypatch):
    monkeypatch.setattr(iface, "get_market_breadth_history", lambda *a, **k: {
        "as_of_date": ANALYSIS, "series": [
            {"trade_date": AS_OF, "up_ratio": 0.5, "limit_up": 60, "market_amount": 8e5},
            {"trade_date": "2026-09-12", "up_ratio": 0.9, "limit_up": 999,
             "market_amount": 9e5},
        ]})
    monkeypatch.setattr(iface, "get_market_fund_flow_history", lambda *a, **k: {
        "as_of_date": "2026-09-14", "series": [
            {"trade_date": AS_OF, "main_net_amount": -1000.0},
            {"trade_date": "2026-09-14", "main_net_amount": 99999.0},
        ]})
    monkeypatch.setattr(iface, "get_margin_trading_history", lambda *a, **k: {
        "as_of_date": ANALYSIS, "series": [
            {"trade_date": AS_OF, "rzye": 1.6e12},
            {"trade_date": "2026-09-12", "rzye": 9.9e12},
        ]})

    feats = mf.build_cn_technical_features(ANALYSIS)
    assert feats["future_data_detected"] is True
    breadth = feats["derived_metrics"]["breadth"]
    assert breadth["as_of_date"] == AS_OF
    assert breadth["series"]["limit_up"]["latest"] == 60  # 999 未进入
    assert feats["derived_metrics"]["fund_flow"]["as_of_date"] == AS_OF
    assert feats["derived_metrics"]["margin"]["as_of_date"] == AS_OF


def test_future_liquidity_month_discarded(monkeypatch):
    """月度数据（YYYYMM）晚于分析月 → 丢弃并置位 future_data_detected。"""
    monkeypatch.setattr(iface, "get_cn_liquidity_indicators", lambda *a, **k: {
        "as_of_date": AS_OF,
        "shibor": {"trade_dates": [], "on": [], "1w": []},
        "lpr": {},
        "money_supply": {"months": ["202608", "202610"], "m1_yoy": [4.6, 9.9],
                         "m2_yoy": [6.2, 9.9], "m1_mom": [0.1, 0.9], "m2_mom": [0.2, 0.9]},
        "missing": {}, "notes": [],
    })
    feats = mf.build_cn_technical_features(ANALYSIS)
    ms = feats["derived_metrics"]["liquidity"]["money_supply"]
    assert ms["as_of_month"] == "202608"
    assert feats["future_data_detected"] is True
    assert any("已丢弃" in note for note in feats["notes"])


# ==================== 3. 样本不足 → None ====================

def test_insufficient_samples_yield_none(monkeypatch):
    monkeypatch.setattr(iface, "get_market_breadth_history",
                        lambda *a, **k: _breadth_payload(n=3))
    feats = mf.build_cn_technical_features(ANALYSIS)
    up_ratio = feats["derived_metrics"]["breadth"]["series"]["up_ratio"]
    assert up_ratio["latest"] == 0.5
    assert up_ratio["ma"][5]["value"] is None
    assert feats["derived_metrics"]["evidence_coverage"]["short_term"]["satisfied"] is False


def test_window_metric_none_when_insufficient():
    assert mf._window_metric([1.0, 2.0], 5, mf._mean)["value"] is None
    assert mf._window_metric([], 1, mf._mean)["value"] is None
    assert mf._window_metric([1.0, 2.0, 3.0], 3, mf._mean)["value"] == pytest.approx(2.0)


def test_all_inputs_unavailable_degrades_without_raising(monkeypatch):
    feats = mf.build_cn_technical_features(ANALYSIS)
    assert feats["as_of_date"] is None
    assert feats["data_quality"]["degradation_level"] == "insufficient"
    assert set(feats["derived_metrics"]["evidence_coverage"]) == {
        "short_term", "wave", "long_term"}
    # 全部缺失时仍输出可读证据块（头部标题 + 信息不足说明），不抛异常
    evidence = mf.format_cn_technical_evidence(feats)
    assert evidence.startswith("# 市场技术特征")
    assert "信息不足" in evidence
    # 特征层整体不可用（无 derived_metrics）→ 返回降级说明句（非 Markdown 块）
    for formatter, kwargs in (
        (mf.format_cn_technical_evidence, {}),
        (mf.format_cn_event_calendar_evidence, {}),
        (mf.format_global_risk_evidence, {}),
    ):
        for bad in (None, {}, {"derived_metrics": None}):
            text = formatter(bad)
            assert text and not text.lstrip().startswith("#")
            assert "不可用" in text


# ==================== 4. 成交额回退（指数 amount → 宽度成交额） ====================

def test_turnover_uses_index_amount_when_available(monkeypatch):
    monkeypatch.setattr(iface, "get_market_breadth_history",
                        lambda *a, **k: _breadth_payload())
    feats = _tech_features(monkeypatch, {"000001.SH": _closes(30),
                                        "399001.SZ": _closes(30)})
    turnover = feats["derived_metrics"]["turnover"]
    assert turnover["source"].startswith("index_amount:")
    assert turnover["samples"] == 30


def test_turnover_falls_back_to_breadth_market_amount(monkeypatch):
    monkeypatch.setattr(iface, "get_market_breadth_history",
                        lambda *a, **k: _breadth_payload(n=25))
    feats = _tech_features(monkeypatch, {"000001.SH": _closes(30)},
                           with_amount=False)
    turnover = feats["derived_metrics"]["turnover"]
    assert turnover["source"] == "breadth_market_amount"
    assert turnover["samples"] == 25
    assert turnover["series"]["latest"] == pytest.approx(8.0e5)
    assert any("成交额回退" in note for note in feats["notes"])
    coverage = feats["derived_metrics"]["evidence_coverage"]["short_term"]
    assert "turnover" in coverage["available"]


# ==================== 5. 阈值常量（集中 + 行为） ====================

def test_threshold_constants_declared_at_module_top():
    text = (PROJECT_ROOT / "AI/dataflows/market_features.py").read_text(encoding="utf-8")
    first_def = text.index("\ndef ")
    for name in ("DEFAULT_TECH_LOOKBACKS", "DEFAULT_CALENDAR_WINDOWS",
                 "DEFAULT_GLOBAL_LOOKBACKS", "SWING_LOOKBACK",
                 "IPO_AMOUNT_HIGH_YI", "IPO_AMOUNT_MEDIUM_YI",
                 "UNLOCK_RATIO_MAX_HIGH", "UNLOCK_RATIO_MAX_MEDIUM",
                 "UNLOCK_WINDOW_SUM_HIGH", "UNLOCK_WINDOW_SUM_MEDIUM",
                 "UNLOCK_COUNT_HIGH", "UNLOCK_COUNT_MEDIUM",
                 "EXPIRY_NEAR_TRADING_DAYS", "EXPIRY_MID_TRADING_DAYS",
                 "MARGIN_CHANGE_RATE_HIGH", "MARGIN_CHANGE_RATE_MEDIUM",
                 "PRESSURE_SCORE_HIGH", "PRESSURE_SCORE_MEDIUM",
                 "VALUATION_PCT_LOW", "VALUATION_PCT_HIGH", "VALUATION_MIN_SAMPLES",
                 "LIQUIDITY_RATE_CHANGE_BP", "LIQUIDITY_M2_CHANGE_PCT",
                 "GLOBAL_RISK_CORE_MIN_RATIO", "REALIZED_VOL_MIN_SAMPLES",
                 "MIN_EVIDENCE_GROUPS", "RISK_GATE_FALLBACK"):
        assert text.index(f"{name} =") < first_def, f"{name} 未集中在文件顶部常量区"


def test_threshold_constant_values():
    assert mf.DEFAULT_TECH_LOOKBACKS == (5, 20, 60, 120, 250)
    assert mf.DEFAULT_CALENDAR_WINDOWS == (5, 20, 60)
    assert mf.DEFAULT_GLOBAL_LOOKBACKS == (1, 5, 20)
    assert mf.INDEX_UNIVERSE[0] == "000001.SH"
    assert set(mf.STYLE_PAIRS) == {"size", "growth_value"}
    assert mf.MIN_EVIDENCE_GROUPS == 2
    assert mf.RISK_GATE_FALLBACK == "caution"
    assert mf.PRESSURE_SCORE_HIGH > mf.PRESSURE_SCORE_MEDIUM
    assert mf.IPO_AMOUNT_HIGH_YI > mf.IPO_AMOUNT_MEDIUM_YI


def _event_features(monkeypatch, ipo=None, unlocks=None, expiry=None, margin=None):
    monkeypatch.setattr(iface, "get_cn_event_calendar", lambda *a, **k: {
        "as_of_date": AS_OF, "ipo": ipo or [], "unlocks": unlocks or [],
        "expiry": expiry or [], "holiday_windows": [], "macro_releases": [],
        "missing": {}, "notes": [],
    })
    if margin is not None:
        monkeypatch.setattr(iface, "get_margin_trading_history",
                            lambda *a, **k: {"as_of_date": AS_OF, "series": margin})
    return mf.build_cn_event_calendar_features(ANALYSIS)


def test_ipo_amount_thresholds(monkeypatch):
    """IPO 募资额合计：≥150 亿 = 高分（4）；≥60 亿 = 中（3）；否则最低分。"""
    near = mf._nth_trading_day_after(ANALYSIS, 2)
    high = _event_features(monkeypatch, ipo=[
        {"ts_code": "A", "name": "甲", "list_date": near, "market_amount": 160.0}])
    assert high["derived_metrics"]["windows_pressure"][5]["level"] == "高"
    med = _event_features(monkeypatch, ipo=[
        {"ts_code": "B", "name": "乙", "list_date": near, "market_amount": 70.0}])
    assert med["derived_metrics"]["windows_pressure"][5]["level"] == "中"
    low = _event_features(monkeypatch, ipo=[
        {"ts_code": "C", "name": "丙", "list_date": near, "market_amount": 10.0}])
    assert low["derived_metrics"]["windows_pressure"][5]["level"] == "低"


def test_ipo_amount_missing_lowers_confidence_not_invented(monkeypatch):
    near = mf._nth_trading_day_after(ANALYSIS, 2)
    feats = _event_features(monkeypatch, ipo=[
        {"ts_code": "A", "name": "甲", "list_date": near, "market_amount": None}])
    entry = feats["derived_metrics"]["windows_pressure"][5]
    assert entry["confidence"] == "low"
    assert any("募资额" in item for item in entry["missing"])


def test_unlock_ratio_thresholds_and_no_forced_sell(monkeypatch):
    near = mf._nth_trading_day_after(ANALYSIS, 3)
    high = _event_features(monkeypatch, unlocks=[
        {"ts_code": "X", "float_date": near, "float_ratio": 25.0}])
    assert high["derived_metrics"]["windows_pressure"][5]["level"] == "高"
    low = _event_features(monkeypatch, unlocks=[
        {"ts_code": "Y", "float_date": near, "float_ratio": 2.0}])
    assert low["derived_metrics"]["windows_pressure"][5]["level"] == "低"
    # 解禁为日历事实，驱动文案不得写成必然卖压
    detail = " ".join(d["detail"] for d in
                      high["derived_metrics"]["windows_pressure"][5]["drivers"])
    assert "解禁" in detail and "必然" not in detail
    assert "不得写成必然卖压" in mf.format_cn_event_calendar_evidence(high)


def test_margin_deleveraging_threshold(monkeypatch):
    """融资余额 20 日降幅 ≥2% → 两融压力高分。"""
    rzye_rows = []
    dates = _trading_dates(21)
    for i, d in enumerate(dates):
        value = 1.0e12 * (1.0 - 0.03 * i / 20.0)  # 末值较 20 日前 -3%
        rzye_rows.append({"trade_date": d, "rzye": value})
    feats = _event_features(monkeypatch, margin=rzye_rows)
    driver = feats["derived_metrics"]["margin"]
    assert driver["change_rate"] is not None and driver["change_rate"] < 0
    margin_drivers = [d for d in feats["derived_metrics"]["windows_pressure"][20]["drivers"]
                      if d["source"] == "margin"]
    assert margin_drivers and margin_drivers[0]["score"] == mf.PRESSURE_SCORE_HIGH


def test_valuation_thresholds(monkeypatch):
    """估值分位：<30 低估、>70 高估、中间合理；样本 < 250 → 信息不足。"""
    def _payload(samples, pct_value):
        # 分位由 `_pct_rank(history, latest)` 计算：100 个 10.0 + 100 个 20.0 +
        # 100 个 30.0，末值替换为 pct_value → 末值分位可控。
        pe = [10.0] * 100 + [20.0] * 100 + [30.0] * 100
        pe = pe[:samples]
        pe[-1] = pct_value
        pb = [1.0] * samples
        return {"as_of_date": AS_OF, "index_valuation": {"000300.SH": {
            "trade_dates": _trading_dates(samples), "pe_ttm": pe,
            "pb": pb, "first_date": _trading_dates(samples)[0],
            "last_date": AS_OF}}, "all_a_snapshot": None, "missing": {}, "notes": []}

    monkeypatch.setattr(iface, "get_market_valuation",
                        lambda *a, **k: _payload(mf.VALUATION_MIN_SAMPLES - 1, 10.0))
    thin = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["valuation"]
    assert thin["level"] == "信息不足"

    # 末值 5.0 → 分位 ~0.2（< 30）→ 低估
    monkeypatch.setattr(iface, "get_market_valuation",
                        lambda *a, **k: _payload(300, 5.0))
    cheap = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["valuation"]
    assert cheap["level"] == "低估"
    assert cheap["index"]["000300.SH"]["pe_ttm_pct"] < mf.VALUATION_PCT_LOW

    # 末值 15.0 → 分位 ~33.5（30~70）→ 合理
    monkeypatch.setattr(iface, "get_market_valuation",
                        lambda *a, **k: _payload(300, 15.0))
    fair = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["valuation"]
    assert fair["level"] == "合理"
    assert mf.VALUATION_PCT_LOW < fair["index"]["000300.SH"]["pe_ttm_pct"] < mf.VALUATION_PCT_HIGH

    # 末值 99.0 → 分位 100（> 70）→ 高估
    monkeypatch.setattr(iface, "get_market_valuation",
                        lambda *a, **k: _payload(300, 99.0))
    rich = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["valuation"]
    assert rich["level"] == "高估"
    assert rich["index"]["000300.SH"]["pe_ttm_pct"] > mf.VALUATION_PCT_HIGH


def test_liquidity_direction_thresholds(monkeypatch):
    """Shibor 1W 5 日变化 ≥ +5bp → 偏紧；≤ -5bp → 偏松。"""
    dates = _trading_dates(21)

    def _payload(rate_start, rate_end):
        rates = [rate_start + (rate_end - rate_start) * i / 20.0 for i in range(21)]
        return {"as_of_date": AS_OF,
                "shibor": {"trade_dates": dates, "on": rates, "1w": rates,
                           "1m": rates, "3m": rates, "1y": rates},
                "lpr": {}, "money_supply": {}, "missing": {}, "notes": []}

    monkeypatch.setattr(iface, "get_cn_liquidity_indicators",
                        lambda *a, **k: _payload(1.50, 2.00))
    tight = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["liquidity"]
    assert tight["direction"] == "偏紧"

    monkeypatch.setattr(iface, "get_cn_liquidity_indicators",
                        lambda *a, **k: _payload(2.00, 1.50))
    loose = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["liquidity"]
    assert loose["direction"] == "偏松"

    monkeypatch.setattr(iface, "get_cn_liquidity_indicators",
                        lambda *a, **k: _payload(1.50, 1.50))
    flat = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["liquidity"]
    assert flat["direction"] == "中性"

    # 5 日变化 0.05bp（< 5bp 阈值）→ 不构成信号 → 中性
    monkeypatch.setattr(iface, "get_cn_liquidity_indicators",
                        lambda *a, **k: _payload(1.50, 1.5005))
    tiny = mf.build_cn_technical_features(ANALYSIS)["derived_metrics"]["liquidity"]
    assert tiny["direction"] == "中性"
    assert tiny["shibor"]["w1"]["change"][5]["value"] < mf.LIQUIDITY_RATE_CHANGE_BP / 100.0


def test_realized_vol_min_samples(monkeypatch):
    """已实现波动率样本 < REALIZED_VOL_MIN_SAMPLES → 不输出（信息不足）。"""
    dates = _trading_dates(mf.REALIZED_VOL_MIN_SAMPLES - 1)
    monkeypatch.setattr(iface, "get_global_risk_indicators", lambda *a, **k: {
        "as_of_date": AS_OF,
        "global_indices": {"SPX": {"trade_dates": dates,
                                   "close": _closes(len(dates)), "field": "close"}},
        "missing": {}, "notes": []})
    feats = mf.build_global_risk_features(ANALYSIS)
    assert feats["derived_metrics"]["volatility"] == {}

    dates = _trading_dates(40)
    monkeypatch.setattr(iface, "get_global_risk_indicators", lambda *a, **k: {
        "as_of_date": AS_OF,
        "global_indices": {"SPX": {"trade_dates": dates,
                                   "close": _closes(len(dates)), "field": "close"}},
        "missing": {}, "notes": []})
    feats = mf.build_global_risk_features(ANALYSIS)
    assert feats["derived_metrics"]["volatility"]["value"] is not None
    assert feats["derived_metrics"]["volatility"]["proxy_for"] == "VIX"


def test_global_risk_coverage_ratio(monkeypatch):
    """覆盖度：核心项 ≥ GLOBAL_RISK_CORE_MIN_RATIO 才视为 ok。"""
    dates = _trading_dates(30)

    def _series(field):
        return {"trade_dates": dates, "field": field, field: _closes(30)}

    full = {"as_of_date": AS_OF,
            "us_treasury": {"y10": _series("y10"), "y2": _series("y2")},
            "global_indices": {"SPX": _series("close")},
            "fx": {"USDCNH.FXCM": _series("bid_close")},
            "commodities": {"SC.INE": _series("close")},
            "missing": {}, "notes": []}
    monkeypatch.setattr(iface, "get_global_risk_indicators", lambda *a, **k: full)
    coverage = mf.build_global_risk_features(ANALYSIS)["derived_metrics"]["coverage"]
    assert set(coverage["available"]) == set(mf.GLOBAL_RISK_CORE_ITEMS)
    assert coverage["ratio"] == 1.0
    assert coverage["status"] == "ok"

    monkeypatch.setattr(iface, "get_global_risk_indicators", lambda *a, **k: {
        "as_of_date": AS_OF, "global_indices": {"SPX": _series("close")},
        "missing": {}, "notes": []})
    thin = mf.build_global_risk_features(ANALYSIS)["derived_metrics"]["coverage"]
    assert thin["status"] == "insufficient"
    assert thin["ratio"] < mf.GLOBAL_RISK_CORE_MIN_RATIO
    assert set(thin["missing"]) == set(mf.GLOBAL_RISK_CORE_ITEMS) - set(thin["available"])


# ==================== 6. market_data_quality 单点写入 ====================

def test_quality_snapshot_core_status_matrix():
    ok = _quality_snapshot()
    assert ok["degradation_level"] == "ok"
    assert ok["core_insufficient"] is False

    missing_core = _quality_snapshot(core_status="missing")
    assert missing_core["degradation_level"] == "insufficient"
    assert missing_core["core_insufficient"] is True
    assert set(missing_core["core_missing_inputs"]) == set(mf.CORE_DATASET_NAMES)

    measured_without_date = _quality_snapshot(core_status="ok", core_actual=None,
                                              measured=True)
    assert measured_without_date["degradation_level"] == "insufficient"

    # 非核心缺失 → partial（不升级为 insufficient）
    datasets = _quality_snapshot()
    non_core = next(n for n, m in mf.MARKET_DATASETS.items() if not m.get("core"))
    datasets["datasets"][non_core]["status"] = "missing"
    partial = mf.build_data_quality_summary(ANALYSIS, datasets["datasets"])
    assert partial["degradation_level"] == "partial"
    assert partial["core_insufficient"] is False
    assert non_core in partial["missing_inputs"]

    # 未注入数据集 → fail-closed 全缺失
    empty = mf.build_data_quality_summary(ANALYSIS)
    assert empty["degradation_level"] == "insufficient"
    assert empty["core_insufficient"] is True
    assert empty["datasets"] and all(
        e["status"] == "missing" for e in empty["datasets"].values())


def test_market_data_quality_written_only_at_graph_start():
    """单点写入：State 写入仅出现在 propagation.py（节点**只读**消费）。

    T6 起门控节点 `market:Risk Gate` 按设计只读该字段（`state.get(...)` 作
    规则表输入），故对市场层的约束从"不得出现该标识"细化为"不得出现写入形态"。
    """
    writers = []
    for path in (PROJECT_ROOT / "AI").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r'["\']market_data_quality["\']\s*:', text):
            writers.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert writers == ["AI/graph/propagation.py"]

    # 写入形态 = dict 键字面量 / 下标赋值；只读（`.get("market_data_quality")`）不匹配
    for path in (PROJECT_ROOT / "AI/marketAgents").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'["\']market_data_quality["\']\s*[:=]', text), (
            f"{path.relative_to(PROJECT_ROOT).as_posix()}: 市场层不得写入 market_data_quality")


def test_market_data_quality_declared_without_reducer():
    """AgentState 声明为 plain dict 元数据（无 reducer 冲突）。"""
    text = (PROJECT_ROOT / "AI/stockAgents/utils/agent_states.py").read_text(encoding="utf-8")
    assert re.search(r'market_data_quality: Annotated\[dict,\s*"', text)
    assert re.search(r'market_regime: Annotated\[dict,\s*"', text)
    assert re.search(r'market_event_calendar: Annotated\[dict,\s*"', text)


# ==================== 7. 门控（第十二章有序规则表） ====================

@pytest.fixture
def quality_ok():
    return _quality_snapshot()


@pytest.fixture
def quality_bad():
    return _quality_snapshot(core_status="missing")


@pytest.mark.parametrize("case,expected_gate,expected_rule", [
    ("rule1_high_medium", "block", 1),
    ("rule1_high_high", "block", 1),
    # 评审 M3：confidence=low / 缺失不再「落规则 5 normal」（旧实现漏判，
    # systemic=high + confidence 缺失会直落 normal）→ 判据 (c) → caution
    ("rule1_low_confidence_degrades_to_caution", "caution", 3),
    ("rule1_missing_confidence_degrades_to_caution", "caution", 3),
    ("rule1_str_number_confidence_blocks", "block", 1),
    ("rule2_short_avoid", "block", 2),
    ("rule4_systemic_medium", "caution", 4),
    ("rule4_short_caution", "caution", 4),
    ("rule4_wave_defense", "caution", 4),
    ("rule5_normal", "normal", 5),
])
def test_gate_rule_table(case, expected_gate, expected_rule, quality_ok):
    gra = _gra("low", "medium")
    regime = _regime()
    if case == "rule1_high_medium":
        gra = _gra("high", "medium")
    elif case == "rule1_high_high":
        gra = _gra("high", "high")
    elif case == "rule1_low_confidence_degrades_to_caution":
        gra = _gra("high", "low")
    elif case == "rule1_missing_confidence_degrades_to_caution":
        gra = {"systemic_risk": "high"}
    elif case == "rule1_str_number_confidence_blocks":
        gra = _gra("high", "0.85")  # 字符串数字置信度归一为 high（评审 M3）
    elif case == "rule2_short_avoid":
        regime = _regime(short="回避")
    elif case == "rule4_systemic_medium":
        gra = _gra("medium", "medium")
    elif case == "rule4_short_caution":
        regime = _regime(short="谨慎")
    elif case == "rule4_wave_defense":
        regime = _regime(wave="防御")
    decision = mf.risk_gate_decision(gra, regime, quality_ok)
    assert decision["gate"] == expected_gate
    assert decision["rule"] == expected_rule


def test_gate_quality_ceiling_never_blocks(quality_bad):
    """规则 3：核心质量不足 → caution 全局上限（即便 systemic=high 也不得 block）。"""
    decision = mf.risk_gate_decision(_gra("high", "high"), _regime(), quality_bad)
    assert decision["gate"] == "caution"
    assert decision["rule"] == 3


def test_gate_short_avoid_with_quality_bad_is_capped(quality_bad):
    """质量不足时短线=回避也不得 block（规则 2 要求核心质量非不足）。"""
    assert mf.derive_risk_gate(_gra("low"), _regime(short="回避"), quality_bad) == "caution"


def test_gate_output_insufficient_enums(quality_ok):
    """判据 (d)：systemic_risk=insufficient / 短线=信息不足 → caution。"""
    bad = _gra("insufficient", "medium", dq=dict(DQ_OK, degradation_level="insufficient"))
    assert mf.derive_risk_gate(bad, _regime(), quality_ok) == "caution"
    bad_regime = _regime(short="信息不足", dq=dict(DQ_OK,
                                                  degradation_level="insufficient"))
    assert mf.derive_risk_gate(_gra("low"), bad_regime, quality_ok) == "caution"


def test_gate_structure_parse_failure_is_caution(quality_ok):
    """判据 (c)：结构解析失败（枚举缺失）→ caution，不再 fail-open normal。"""
    assert mf.derive_risk_gate({}, {}, quality_ok) == "caution"
    assert mf.derive_risk_gate(_gra("low"), {}, quality_ok) == "caution"


def test_gate_quality_insufficient_is_union_of_snapshot_and_node(quality_bad):
    """判据 (a)+(b) 取并集（评审 M2）：节点自评 ok 不得清零启动快照的不足。

    修复前节点 data_quality=ok 会覆盖快照的 insufficient（判据 a 被绕过），
    核心输入缺失时规则 3 的 caution 全局上限失效。
    """
    gra = _gra("low", "medium", dq=DQ_OK)
    regime = _regime(dq=DQ_OK)
    # 快照不足 + 节点标注 ok → 仍 caution（修复前的漏洞路径）
    assert mf.derive_risk_gate(gra, regime, quality_bad) == "caution"
    # 无快照（图启动未写入）+ 节点标注 ok → fail-closed → caution
    assert mf.derive_risk_gate(gra, regime, {}) == "caution"
    # 快照 ok + 节点 ok → normal（并集不引入额外升级）
    assert mf.derive_risk_gate(gra, regime, _quality_snapshot()) == "normal"


def test_gate_node_subfield_insufficient_escalates(quality_ok):
    gra = _gra("low", "medium", dq=dict(DQ_OK, degradation_level="insufficient"))
    assert mf.derive_risk_gate(gra, _regime(dq=DQ_OK), quality_ok) == "caution"


def test_gate_no_snapshot_fail_closed(quality_ok):
    """无快照且节点未标注 → 视为不足（fail-closed → caution）。"""
    assert mf.derive_risk_gate(_gra("low"), _regime(), {}) == "caution"


def test_gate_only_accepts_three_values(quality_ok):
    for gate in (mf.derive_risk_gate(_gra("low"), _regime(), quality_ok),
                 mf.derive_risk_gate(_gra("high", "high"), _regime(), quality_ok),
                 mf.derive_risk_gate(_gra("medium"), _regime(), quality_ok)):
        assert gate in mf.RISK_GATE_ENUM


# ==================== 8. 结构化解析与节点 data_quality ====================

def test_parse_market_regime_json():
    report = ('```json\n{"short_term": {"level": "谨慎", "evidence": "宽度转弱",'
              '"confidence": "中"}, "wave": {"level": "防御"},'
              '"long_term": {"level": "等待窗口"}, "style": "大盘价值",'
              '"sentiment_cycle": "退潮"}\n```')
    parsed = mf.parse_market_regime(report)
    assert parsed["short_term"]["level"] == "谨慎"
    assert parsed["short_term"]["confidence"] == "medium"
    assert parsed["wave"]["level"] == "防御"
    assert parsed["style"] == "大盘价值"
    assert parsed["sentiment_cycle"] == "退潮"
    assert parsed["parse_status"] == "ok"


def test_parse_market_regime_legacy_text_degraded_but_usable():
    legacy = ("## 〇、市场环境速览\n市场状态标签: 震荡偏弱\n情绪周期位置: 退潮\n"
              "三级别判定:\n- 短线: 谨慎 建议仓位5成 — 宽度转弱\n- 波段: 平衡\n"
              "- 长线: 配置窗口 风格方向: 大盘价值\n")
    parsed = mf.parse_market_regime(legacy)
    assert parsed["short_term"]["level"] == "谨慎"
    assert parsed["wave"]["level"] == "平衡"
    assert parsed["long_term"]["level"] == "配置窗口"
    assert parsed["parse_status"] == "degraded"


def test_parse_market_regime_garbage_degrades_to_insufficient():
    parsed = mf.parse_market_regime("完全不符合模板的报告" * 5)
    assert parsed["short_term"]["level"] == "信息不足"
    assert parsed["parse_status"] == "degraded"
    assert parsed["parse_note"]


def test_parse_event_calendar_json_and_legacy():
    report = (
        '{"short_term": {"level": "高", "score": 4, "drivers": ["解禁峰值"],'
        '"key_dates": ["2026-09-16"]}, "wave": {"level": "中", "score": 3},'
        '"long_term": {"level": "低", "score": 1}}')
    parsed = mf.parse_market_event_calendar(report)
    assert parsed["short_term"]["score"] == 4
    assert parsed["short_term"]["drivers"] == ["解禁峰值"]
    assert parsed["long_term"]["score"] == 1
    assert parsed["parse_status"] == "ok"

    legacy = ("短线(5日): 风险高 资金压力4分 关键时点: 09-16 解禁峰值\n"
              "波段(20日): 风险中 资金压力2分\n")
    parsed = mf.parse_market_event_calendar(legacy)
    assert parsed["short_term"]["level"] == "高"
    assert parsed["short_term"]["score"] == 4
    assert parsed["parse_status"] == "degraded"


@pytest.mark.parametrize("text,expected", [
    # 否定探针（评审 M1）：子串盲配会把「不高」反配为 high → 触发 block
    ("不高", "insufficient"),
    ("风险不高", "insufficient"),
    ("不低", "insufficient"),
    ("非高", "insufficient"),
    ("无高风险信号", "insufficient"),
    # 正向命中不受影响
    ("高", "high"),
    ("低", "low"),
    ("信息不足", "insufficient"),
    # 同一 token 逐次判定：取非否定出现（「不高，但尾部风险高」→ 高）
    ("不高，但尾部风险高", "high"),
])
def test_negation_probe_systemic_risk_not_flipped(text, expected):
    """系统性风险：否定式（「不高」/「不低」）不得反配为相反级别（评审 M1）。

    降级为 insufficient → 门控判据 (d) → caution，而不是 block（修复前
    「不高」子串命中 → high + confidence 缺失 → 旧实现还直落 normal）。
    """
    assessment = mf.parse_global_risk_assessment(
        json.dumps({"systemic_risk": text, "confidence": "medium"},
                   ensure_ascii=False))
    assert assessment["systemic_risk"] == expected


@pytest.mark.parametrize("text", ["不适合", "不谨慎", "不回避"])
def test_negation_probe_regime_levels_degrade(text):
    """三级别判定：否定式（「不适合」）→ 信息不足，不反配为对应级别（评审 M1）。"""
    parsed = mf.parse_market_regime(json.dumps({
        "short_term": {"level": text}, "wave": {"level": "进攻"},
        "long_term": {"level": "配置窗口"}}, ensure_ascii=False))
    assert parsed["short_term"]["level"] == "信息不足"
    # 正向仍在（对照组）
    parsed_ok = mf.parse_market_regime(json.dumps({
        "short_term": {"level": "适合"}, "wave": {"level": "进攻"},
        "long_term": {"level": "配置窗口"}}, ensure_ascii=False))
    assert parsed_ok["short_term"]["level"] == "适合"


def test_negation_probe_risk_appetite_and_calendar():
    """枚举子串归一：「不适合」「不高」均降级信息不足（评审 M1 探针）。"""
    assessment = mf.parse_global_risk_assessment(
        json.dumps({"risk_appetite": "不适合", "systemic_risk": "low",
                    "confidence": "medium"}, ensure_ascii=False))
    assert assessment["risk_appetite"] == "信息不足"

    calendar = mf.parse_market_event_calendar(json.dumps({
        "short_term": {"level": "不高"}, "wave": {"level": "中"},
        "long_term": {"level": "低"}}, ensure_ascii=False))
    assert calendar["short_term"]["level"] == "信息不足"
    assert calendar["wave"]["level"] == "中"


def test_negation_probe_legacy_text_level():
    """旧文本兜底路径同样受否定判定保护（「短线: 不高」→ 信息不足）。"""
    parsed = mf.parse_market_regime("三级别判定:\n- 短线: 不高\n- 波段: 平衡\n")
    assert parsed["short_term"]["level"] == "信息不足"
    assert parsed["wave"]["level"] == "平衡"


@pytest.mark.parametrize("value,expected", [
    ("0.85", "high"),      # 字符串数字（旧实现不可解析 → 直落 normal，评审 M3）
    ("0.5", "medium"),
    ("0.2", "low"),
    (0.85, "high"),
    (85, "high"),          # 百分数写法
    ("85%", "high"),
    ("0.4", "medium"),     # 边界：≥0.4 medium，≥0.7 high
    ("0.39", "low"),
    ("high", "high"),      # 别名
    ("中", "medium"),
    ("不高", None),        # 否定式不匹配别名（评审 M1）
    ("abc", None),         # 不可解析 → None → 判据 (c)
    (None, None),
    (True, None),          # bool 不是数值置信度
])
def test_confidence_parsing_covers_str_numbers(value, expected):
    """置信度归一：字符串数字/百分数/别名可解析，不可解析 → None（评审 M3）。"""
    assert mf._norm_confidence(value) == expected


def test_str_confidence_flows_through_parser_and_gate():
    """端到端：`confidence="0.85"` 经解析 → high → 门控规则 1 block（评审 M3）。"""
    assessment = mf.parse_global_risk_assessment(
        json.dumps({"systemic_risk": "high", "confidence": "0.85"},
                   ensure_ascii=False))
    assert assessment["confidence"] == "high"
    dq_ok = _quality_snapshot()
    gra_dq = {k: v for k, v in assessment.items()
              if k in ("systemic_risk", "confidence")}
    gra_dq["data_quality"] = DQ_OK
    regime_dq = mf.parse_market_regime(json.dumps({
        "short_term": {"level": "适合"}, "wave": {"level": "进攻"},
        "long_term": {"level": "配置窗口"}}, ensure_ascii=False))
    regime_dq["data_quality"] = DQ_OK
    assert mf.derive_risk_gate(gra_dq, regime_dq, dq_ok) == "block"


def test_node_data_quality_marks_output_insufficient():
    features = {"as_of_date": AS_OF, "data_quality": dict(DQ_OK)}
    good = mf.parse_market_regime(
        '{"short_term": {"level": "适合"}, "wave": {"level": "进攻"},'
        '"long_term": {"level": "配置窗口"}}')
    dq = mf.node_data_quality(features, good)
    assert dq["degradation_level"] == "ok"
    assert dq["as_of_date"] == AS_OF

    # 判据 (d)：短线输出“信息不足” → 子字段必须标注 insufficient
    insufficient = mf.parse_market_regime(
        '{"short_term": {"level": "信息不足"}, "wave": {"level": "进攻"},'
        '"long_term": {"level": "配置窗口"}}')
    dq = mf.node_data_quality(features, insufficient)
    assert dq["degradation_level"] == "insufficient"
    assert dq["insufficient_levels"] == ["short_term"]

    # 判据 (c)：解析失败 → insufficient
    dq = mf.node_data_quality(features, mf.parse_market_regime("乱码报告" * 10))
    assert dq["degradation_level"] == "insufficient"

    # 特征层缺失 → insufficient（不因 LLM 输出漂亮而判定有数据）
    dq = mf.node_data_quality({"as_of_date": None, "data_quality": {
        "degradation_level": "insufficient", "actual_dates": {}, "missing_inputs": ["x"],
        "future_data_detected": True, "notes": []}}, good)
    assert dq["degradation_level"] == "insufficient"
    assert dq["future_data_detected"] is True


# ==================== 9. 技术隔离（Prompt 组装无事件/新闻输入） ====================

TECH_FORBIDDEN_TOKENS = ("解禁", "IPO", "新股", "募资", "交割", "长假", "新闻", "CAR",
                         "事件日历")


def test_technical_evidence_contains_no_event_content(monkeypatch):
    monkeypatch.setattr(iface, "get_market_breadth_history",
                        lambda *a, **k: _breadth_payload())
    monkeypatch.setattr(iface, "get_margin_trading_history", lambda *a, **k: {
        "as_of_date": AS_OF,
        "series": [{"trade_date": d, "rzye": 1.6e12} for d in _trading_dates(21)]})
    feats = _tech_features(monkeypatch, {"000001.SH": _closes(60),
                                         "399001.SZ": _closes(60)})
    evidence = mf.format_cn_technical_evidence(feats)
    assert evidence.startswith("#")
    for token in TECH_FORBIDDEN_TOKENS:
        assert token not in evidence, f"技术证据块不应包含事件/新闻内容：{token}"


def test_cn_tech_node_reads_no_event_fields():
    """CN Tech 节点不读取事件/新闻/预取字段（技术隔离，方案第十章）。"""
    text = (PROJECT_ROOT / "AI/marketAgents/analysts/cn_tech_analyst.py"
            ).read_text(encoding="utf-8")
    for token in ("market_event_calendar", "cn_news_report", "international_event",
                  "international_news_report", "sector_events", "stock_events",
                  "event_study"):
        assert token not in text, f"CN Tech 节点不应引用 {token}"


def test_cn_news_node_outputs_calendar_without_tech_fields():
    text = (PROJECT_ROOT / "AI/marketAgents/analysts/cn_news_analyst.py"
            ).read_text(encoding="utf-8")
    assert "market_event_calendar" in text
    assert "market_regime" not in text


def test_event_calendar_windows_are_trading_day_windows(monkeypatch):
    """三级别窗口为 5/20/60 交易日，窗口截止日为交易日。"""
    feats = _event_features(monkeypatch)
    pressure = feats["derived_metrics"]["windows_pressure"]
    assert sorted(pressure) == [5, 20, 60]
    for window, entry in pressure.items():
        assert entry["window"] == window
        assert entry["level"] in ("高", "中", "低", "信息不足")
    window_end = pressure[5]["window_end"]
    assert window_end == mf._nth_trading_day_after(ANALYSIS, 5)
    assert date.fromisoformat(window_end).weekday() < 5


def test_chinese_negation_patterns():
    """中文枚举否定识别（Code Review 第 2 轮 findings 1/2 回归）。

    假否定（强化/复合词不是否定）不得吞掉枚举；多字否定+填充字必须识别；
    双重否定抵消；基础否定仍生效。
    """
    # 强化/复合词：不是否定，token 应正常命中
    assert mf._substring_token("非常高风险", ("高",)) == "高"
    assert mf._substring_token("非常低", ("低",)) == "低"
    assert mf._substring_token("无非是高位震荡", ("高",)) == "高"
    assert mf._substring_token("未来风险高", ("高",)) == "高"
    # 多字否定 + 填充字：命中即否定
    assert mf._substring_token("风险并没有偏高", ("高",)) is None
    assert mf._substring_token("并没有偏低", ("低",)) is None
    assert mf._substring_token("风险并没有偏高", ("高",)) is None
    # 双重否定抵消：并非不高 = 高
    assert mf._substring_token("并非不高", ("高",)) == "高"
    # 基础否定仍生效
    assert mf._substring_token("不高", ("高",)) is None
    assert mf._substring_token("不低", ("低",)) is None
    # 否定式不吞后续非否定出现（逐次判定）
    assert mf._substring_token("不高，但尾部风险高", ("高",)) == "高"


def test_calendar_unrecognized_level_degrades_parse():
    """日历 level 非枚举且不可识别（如"爆炸"）即便带 score 也必须降级 parse_status。"""
    cal = mf.parse_market_event_calendar(
        '{"short_term": {"level": "爆炸", "score": 4},'
        ' "wave": {"level": "中", "score": 3}, "long_term": {"level": "低", "score": 2}}')
    assert cal["parse_status"] == mf.PARSE_STATUS_DEGRADED
    assert cal["short_term"]["level"] == "信息不足"
    assert "未识别" in cal["parse_note"]
    # 强化词"非常高"不是否定：应正常归一为 高（Code Review 第 2 轮 finding 1）
    cal_ok = mf.parse_market_event_calendar(
        '{"short_term": {"level": "非常高", "score": 4},'
        ' "wave": {"level": "中", "score": 3}, "long_term": {"level": "低", "score": 2}}')
    assert cal_ok["parse_status"] == mf.PARSE_STATUS_OK
    assert cal_ok["short_term"]["level"] == "高"


def test_unparsable_as_of_flagged_in_all_feature_groups(monkeypatch):
    """`curr_date` 不可解析 → 防前视静默失效须显式标注（评审残留）。

    `_split_future` 的截断条件（`norm > as_of`）在 as_of 不可解析时恒不成立，
    未来行被静默保留；修复前还用 `str(curr_date)` 兜底造出垃圾 as_of。三组特征
    必须同口径：notes 标注「防前视未生效」+ `future_data_undeterminable=True`，
    且不得谎报 `future_data_detected`（无法判定 ≠ 已确认无未来数据）。
    """
    bad_date = "交易日待定"
    monkeypatch.setattr(iface, "get_market_index_features",
                        lambda *a, **k: _index_payload({"000001.SH": _closes(30)}))
    monkeypatch.setattr(iface, "get_cn_event_calendar", lambda *a, **k: {
        "as_of_date": AS_OF, "ipo": [], "unlocks": [], "expiry": [],
        "holiday_windows": [], "macro_releases": [], "missing": {}, "notes": []})
    monkeypatch.setattr(iface, "get_global_risk_indicators", lambda *a, **k: {
        "as_of_date": AS_OF,
        "global_indices": {"SPX": {"trade_dates": _trading_dates(30),
                                   "close": _closes(30), "field": "close"}},
        "missing": {}, "notes": []})

    for feats in (mf.build_cn_technical_features(bad_date),
                  mf.build_cn_event_calendar_features(bad_date),
                  mf.build_global_risk_features(bad_date)):
        assert mf._AS_OF_UNPARSABLE_NOTE in feats["notes"]
        assert feats["data_quality"]["future_data_undeterminable"] is True
        assert feats["future_data_undeterminable"] is True
        assert feats["future_data_detected"] is False


def test_format_cn_technical_evidence_renders_data_notes():
    """技术证据块渲染 `notes`（数据源标注，评审残留）。

    未来行丢弃、as_of 不可解析等标注必须随证据块进 Prompt，不得只存在于结构化字段。
    """
    feats = {
        "as_of_date": AS_OF, "analysis_date": ANALYSIS,
        "derived_metrics": {"indices": {}, "style": {}, "turnover": {},
                            "breadth": {}, "fund_flow": {}, "margin": {},
                            "valuation": {}, "liquidity": {},
                            "evidence_coverage": {}},
        "missing_inputs": [],
        "notes": [mf._AS_OF_UNPARSABLE_NOTE, "指数序列含 2 条晚于分析日的数据，已丢弃"],
    }
    block = mf.format_cn_technical_evidence(feats)
    assert "## 数据源标注" in block
    assert mf._AS_OF_UNPARSABLE_NOTE in block
    assert "已丢弃" in block

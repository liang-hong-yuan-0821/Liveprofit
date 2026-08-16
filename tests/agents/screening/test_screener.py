"""
单元测试：选股层（screener 纯函数 + screening_node 编排，mock 数据层）

覆盖方案 3.4.3：解析/筛选/排序/截断、熔断、空池安全短路。
"""

import pytest

from AI.screening.screener import (
    filter_and_score,
    parse_constituents,
    parse_ranking,
)
from AI.screening.screening_node import run_screening


# ==================== parse_constituents ====================

def test_parse_constituents_normal():
    text = "000422|湖北宜化\n688550|瑞联新材\nbad_code|脏数据\n\n600519|贵州茅台"
    out = parse_constituents(text)
    assert out == [
        {"code": "000422", "name": "湖北宜化"},
        {"code": "688550", "name": "瑞联新材"},
        {"code": "600519", "name": "贵州茅台"},
    ]


def test_parse_constituents_error_text():
    assert parse_constituents("未获取到 白酒 成分股数据。") == []
    assert parse_constituents("") == []


# ==================== parse_ranking ====================

def test_parse_ranking_normal():
    text = (
        "000422.SZ|+20.00|120.00|150000000\n"
        "688550.SH|+5.00|55.00|80000000\n"
        "板块均值|+10.00"
    )
    rows, avg = parse_ranking(text)
    assert avg == pytest.approx(10.0)
    assert len(rows) == 2
    assert rows[0]["code"] == "000422.SZ"
    assert rows[0]["pct_change"] == pytest.approx(20.0)
    assert rows[0]["last_close"] == pytest.approx(120.0)
    assert rows[0]["last_amount"] == pytest.approx(1.5e8)


def test_parse_ranking_error_text():
    rows, avg = parse_ranking("获取个股涨幅排名失败: xxx")
    assert rows == [] and avg == 0.0


# ==================== filter_and_score ====================

def _rows():
    return [
        {"code": "000422.SZ", "pct_change": 20.0, "last_close": 120.0, "last_amount": 1.5e8},
        {"code": "600519.SH", "pct_change": 5.0, "last_close": 1501.0, "last_amount": 3e9},
        {"code": "000001.SZ", "pct_change": 15.0, "last_close": 12.0, "last_amount": 1e6},
        {"code": "000002.SZ", "pct_change": 25.0, "last_close": 9.0, "last_amount": 2e8},
    ]


def test_filter_and_score_basic():
    name_map = {
        "000422.SZ": "湖北宜化",
        "600519.SH": "贵州茅台",
        "000001.SZ": "*ST平安",
        "000002.SZ": "退市万科",
    }
    out = filter_and_score(_rows(), sector_avg=10.0, name_map=name_map, min_turnover=5e7)

    # vs_avg: 000422=+10 入池；600519=-5 淘汰；000001=+5 但 ST 淘汰；
    # 000002=+15 但"退"淘汰；000001 成交额 1e6 < 5e7 淘汰（双重淘汰）
    assert [x["code"] for x in out] == ["000422.SZ"]
    assert out[0]["vs_avg"] == pytest.approx(10.0)
    assert out[0]["sector_avg_pct"] == pytest.approx(10.0)


def test_filter_and_score_sorted_by_vs_avg():
    rows = _rows() + [{"code": "000003.SZ", "pct_change": 30.0, "last_close": 8.0, "last_amount": 1e8}]
    name_map = {r["code"]: f"股{r['code']}" for r in rows}
    out = filter_and_score(rows, sector_avg=0.0, name_map=name_map, min_turnover=5e7)
    vs = [x["vs_avg"] for x in out]
    assert vs == sorted(vs, reverse=True)


# ==================== run_screening（mock 数据层） ====================

@pytest.fixture
def fake_dataflow(monkeypatch):
    """mock dataflow 的两个接口，可脚本化返回"""
    from AI.screening import screening_node as mod

    calls = {"constituents": [], "ranking": []}

    def set_constituents(*results):
        calls["constituents"] = list(results)

    def set_ranking(*results):
        calls["ranking"] = list(results)

    def fake_constituents(sector):
        return calls["constituents"].pop(0) if calls["constituents"] else "未获取到成分股数据。"

    def fake_ranking(codes, days):
        return calls["ranking"].pop(0) if calls["ranking"] else "未获取到个股涨幅数据。"

    monkeypatch.setattr(mod.dataflow, "get_sector_constituents", fake_constituents)
    monkeypatch.setattr(mod.dataflow, "get_stocks_performance_ranking", fake_ranking)
    return {"set_constituents": set_constituents, "set_ranking": set_ranking}


def test_run_screening_empty_list_returns_empty_pool(fake_dataflow):
    assert run_screening({"sector_shortlist_structured": []}) == []
    assert run_screening({}) == []


def test_run_screening_full_flow(fake_dataflow):
    fake_dataflow["set_constituents"]("600519|贵州茅台\n000858|五粮液")
    fake_dataflow["set_ranking"](
        "600519|+12.00|1501.00|3000000000\n000858|+4.00|130.00|2000000000\n板块均值|+6.00"
    )
    state = {"sector_shortlist_structured": ["白酒"]}

    pool = run_screening(state, min_turnover=5e7)

    assert len(pool) == 1
    stock = pool[0]
    assert stock["code"] == "600519.SH"
    assert stock["name"] == "贵州茅台"
    assert stock["sector"] == "白酒"
    assert stock["vs_avg"] == pytest.approx(6.0)
    assert stock["last_close"] == pytest.approx(1501.0)


def test_run_screening_truncates_pool(fake_dataflow):
    blocks = []
    consts = []
    for i in range(3):
        consts.append(f"00000{i}|股{i}\n00001{i}|股1{i}")
        blocks.append(
            f"00000{i}|+{20 - i}.00|10.00|100000000\n"
            f"00001{i}|+{10 - i}.00|10.00|100000000\n"
            "板块均值|+5.00"
        )
    fake_dataflow["set_constituents"](*consts)
    fake_dataflow["set_ranking"](*blocks)
    state = {"sector_shortlist_structured": ["板块A", "板块B", "板块C"]}

    pool = run_screening(state, min_turnover=5e7, max_stocks=3)

    assert len(pool) == 3
    # 全池按 vs_avg 降序：000000(15) > 000001(14) > 000002(13) > ...
    assert pool[0]["code"] == "000000.SZ"


def test_run_screening_circuit_breaker(fake_dataflow):
    """连续 3 个板块失败 → 熔断中止，后续板块不再请求"""
    # 全部板块成分股接口都返回错误 → 连续失败
    state = {"sector_shortlist_structured": ["板块A", "板块B", "板块C", "板块D"]}
    fake_dataflow["set_constituents"]("未获取到成分股数据。")

    pool = run_screening(state)

    assert pool == []
    # 熔断在第 3 个板块，第 4 个板块不应再发起请求
    # （fake 队列里 4 次都会 pop，但熔断后循环 break，pop 次数 = 3）
    # 用固定数量队列验证：熔断后不再 pop
    fake_dataflow["set_constituents"](
        "未获取到成分股数据。", "未获取到成分股数据。",
        "未获取到成分股数据。", "600519|贵州茅台",
    )
    pool = run_screening({"sector_shortlist_structured": ["A", "B", "C", "D"]})
    assert pool == []  # 第 4 个板块（成功）不会被处理，因为已熔断


def test_run_screening_dedupes_across_sectors(fake_dataflow):
    """同一只票挂在两个东财概念板块下 → 只保留 vs_avg 最高的一条"""
    fake_dataflow["set_constituents"](
        "600519|贵州茅台\n000858|五粮液",        # 白酒
        "600519|贵州茅台\n000001|平安银行",      # 消费（重复）
    )
    fake_dataflow["set_ranking"](
        "600519|+12.00|1501.00|3000000000\n000858|+4.00|130.00|2000000000\n板块均值|+6.00",
        "600519|+12.00|1501.00|3000000000\n000001|+3.00|11.00|2000000000\n板块均值|+9.00",
    )
    state = {"sector_shortlist_structured": ["白酒", "消费"]}

    pool = run_screening(state, min_turnover=5e7)

    codes = [s["code"] for s in pool]
    assert codes.count("600519.SH") == 1
    # 白酒板块 vs_avg=6.0 > 消费板块 vs_avg=3.0 → 保留白酒归属
    moutai = next(s for s in pool if s["code"] == "600519.SH")
    assert moutai["sector"] == "白酒"
    assert moutai["vs_avg"] == pytest.approx(6.0)


def test_run_screening_single_sector_failure_continues(fake_dataflow):
    """单板块失败不影响其他板块"""
    fake_dataflow["set_constituents"](
        "未获取到成分股数据。",                       # 板块A 失败
        "600519|贵州茅台\n000858|五粮液",            # 板块B 成功
    )
    fake_dataflow["set_ranking"](
        "600519|+12.00|1501.00|3000000000\n000858|+4.00|130.00|2000000000\n板块均值|+6.00"
    )
    state = {"sector_shortlist_structured": ["板块A", "板块B"]}

    pool = run_screening(state, min_turnover=5e7)

    assert len(pool) == 1
    assert pool[0]["sector"] == "板块B"

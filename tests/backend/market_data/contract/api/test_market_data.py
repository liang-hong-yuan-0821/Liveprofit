# test-catalog-begin
# {
#   "purpose": "市场数据契约测试（§2.6.1：bars 参数/错误语义、US/KR 空 bars、热点现场计算）。",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "K线",
#     "每日",
#     "复权因子",
#     "指数",
#     "市场分析",
#     "个股分析",
#     "走势",
#     "market_data",
#     "bars",
#     "daily",
#     "factor",
#     "index",
#     "market",
#     "stock",
#     "trends"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/modules/market_data/infrastructure/calendar_adapter.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

"""市场数据契约测试（§2.6.1：bars 参数/错误语义、US/KR 空 bars、热点现场计算）。

目录端点已删（前端写死清单，决策 4）——test_market_assets_catalog_envelope 随删，
"12 指数 + 组序"回归迁为前端常量断言（polish g）。
seed 改造（3.3.3 定稿）：按 ts_code 直插 market schema（instrument 先行插入、
instrument_daily/factor_daily 用 ts_code），ON CONFLICT DO NOTHING 幂等。
"""

from __future__ import annotations

from tests.backend.market_data.support.market_data import (
    _seed_instrument,
    _seed_bars,
    _seed_dense_bars,
    _seed_dense_factors,
    _seed_sector_daily,
    _seed_concept_tree,
    _seed_sector_daily_bars,
    _seed_dense_sector_bars,
    _seed_stock_bars,
)

from datetime import date, timedelta

import pytest

from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar






def test_bars_success_with_freshness_and_closed(client):
    _seed_bars(client)
    # 注入假日历：最近交易日 = 数据日，非交易日 → CLOSED + FRESH
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-05"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["interval"] == "1d"
    assert data["from"] == "2026-09-01"
    assert data["to"] == "2026-09-05"
    assert len(data["bars"]) == 1
    assert data["bars"][0]["close"] == 3340.0
    # 涨跌幅 = 上游 pct_chg 原值透传（不自算；源未提供时为 null，见 dense 用例）
    assert data["bars"][0]["pct_chg"] == 1.25
    assert data["freshness_status"] == "FRESH"
    assert data["market_session_status"] == "CLOSED"
    assert data["market_closed_reason"] is not None
    assert data["asset"]["symbol"] == "000001.SH"
    # 单根 bars、未 seed 因子行：指标仍在（因子行缺失时为 null，降级语义固化）
    indicators = data["indicators"]
    assert indicators is not None
    assert indicators["ma"][0]["values"] == [None]
    assert indicators["boll"]["mid"] == [None]


def test_bars_param_errors(client):
    # interval 非白名单 → 422
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=5d&from=2026-08-01&to=2026-09-04"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INTERVAL_NOT_SUPPORTED"
    # from>to → 422（365 上限已由 K 线方案移除；from>to 拒绝语义保留，M2 定稿）
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from=2026-09-05&to=2026-09-01"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "RANGE_TOO_LARGE"
    # 未知资产 → 404
    response = client.http.get(
        "/api/v1/market-data/indices/999999.SH/bars?market=CN&interval=1d&from=2026-08-01&to=2026-09-04"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"






def test_bars_include_indicators_aligned_with_factors(client):
    """指标契约（技术指标数据源切换方案 §3.3.3）：指标逐值透传因子读模型（不自算）、与 bars 等长对齐。"""
    _seed_dense_bars(client)
    _seed_dense_factors(client)
    from_date = date(2026, 4, 7) + timedelta(days=90)
    to_date = date(2026, 4, 7) + timedelta(days=149)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=to_date)
    response = client.http.get(
        f"/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from={from_date}&to={to_date}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # 返回区间仍为 [from, to]，共 60 根
    assert len(data["bars"]) == 60
    assert data["bars"][0]["close"] == 3390.0
    # 该 seed 未写 pct_chg（上游缺列）→ null 透传，不从相邻 close 自算
    assert data["bars"][0]["pct_chg"] is None
    # 指标与 bars 等长、按 index 对齐
    indicators = data["indicators"]
    assert indicators is not None
    assert [line["period"] for line in indicators["ma"]] == [5, 10, 20, 60]
    for line in indicators["ma"]:
        assert len(line["values"]) == 60
    for key in ("mid", "upper", "lower"):
        assert len(indicators["boll"][key]) == 60
    # 逐值透传（不自算）：首根指标 == seed 因子值（close=3390.0 处的固定偏移）
    assert indicators["ma"][3]["values"][0] == 3450.0  # ma_bfq_60 = close + 60
    assert indicators["boll"]["mid"][0] == 3392.0  # boll_mid_bfq = close + 2
    # MACD 副图契约：dif/dea/hist 与 bars 等长、hist 为上游 macd_bfq 原值
    macd = indicators["macd"]
    assert macd is not None
    assert (macd["fast"], macd["slow"], macd["signal"]) == (12, 26, 9)
    for key in ("dif", "dea", "hist"):
        assert len(macd[key]) == 60
    assert macd["dif"][0] == 3390.5  # macd_dif_bfq = close + 0.5
    assert macd["hist"][0] == 3390.4  # macd_bfq = close + 0.4（上游原值）
    # 旧字段回归
    assert data["freshness_status"] == "FRESH"
    assert data["market_session_status"] == "CLOSED"
    assert data["asset"]["symbol"] == "000001.SH"


def test_bars_factor_row_with_null_column_falls_back_to_none(client):
    """因子行存在但列为 NULL（NaN 清洗结果）：该位置指标 None、整请求 200 不 500（2026-09-12 Code Review blocker 回归）。"""
    _seed_bars(client, trade_date=date(2026, 9, 4))
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        # ma_bfq_5 列为 NULL，其余因子列有值
        conn.execute(
            text(
                "INSERT INTO market.factor_daily (ts_code, trade_date, ma_bfq_5, ma_bfq_10, "
                "ma_bfq_20, ma_bfq_60, boll_mid_bfq, boll_upper_bfq, boll_lower_bfq, "
                "macd_dif_bfq, macd_dea_bfq, macd_bfq) "
                "VALUES ('000001.SH', :trade_date, NULL, 3310, 3320, 3360, "
                "3302, 3330, 3270, 0.5, 0.3, 0.4)"
            ),
            {"trade_date": date(2026, 9, 4)},
        )
    engine.dispose()

    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-05"
    )
    assert response.status_code == 200
    indicators = response.json()["data"]["indicators"]
    assert indicators["ma"][0]["values"] == [None]  # ma_bfq_5 列 NULL → None
    assert indicators["ma"][1]["values"] == [3310.0]  # 其余列正常取值
    assert indicators["macd"]["hist"] == [0.4]


def test_bars_us_asset_returns_200_empty_bars_unavailable(client):
    """US 资产：instrument 有行而 instrument_daily 无数据 → 200 空 bars +
    freshness_status=UNAVAILABLE（原 503 语义整体删除，决策 13/3.3.1 定稿）。"""
    _seed_instrument(client, ".INX", name="标普500")
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/indices/.INX/bars?market=US&interval=1d&from=2026-08-01&to=2026-09-04"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["bars"] == []
    assert data["freshness_status"] == "UNAVAILABLE"
    assert data["asset"]["market"] == "US"  # market 形参回显
    assert data["asset"]["symbol"] == ".INX"


def test_hot_concepts_empty_is_normal_business_state(client):
    response = client.http.get(
        "/api/v1/market-data/concepts/hot?market=CN&interval=1d&as_of=2026-08-01&limit=20"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "NO_HOT_CONCEPTS"
    assert data["items"] == []
    assert data["algorithm_version"] == "heat_v1"
    assert response.json()["meta"]["schema_version"] == "v1"




def test_hot_concepts_computed_on_the_fly(client):
    """热度现场计算（决策 13）：source='dc' 过滤 ths、score 降序与 limit、
    M3 三段降级、sector_name 映射、from→as_of 透传。"""
    _seed_sector_daily(client)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 7))
    response = client.http.get(
        "/api/v1/market-data/concepts/hot?market=CN&interval=1d&as_of=2026-09-07&limit=2"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "OK"
    assert data["source"] == "dc"  # m5 定稿：与热度口径一致
    assert data["freshness_status"] == "FRESH"
    items = data["items"]
    # limit=2 生效；ths 源被过滤（全部 dc）
    assert len(items) == 2
    assert all(it["sector_code"].startswith("BK") for it in items)
    # score 降序：BK1753（完整公式 15 行）> BK1754（5 行 vol_change=0）
    # BK1753: tail(11) close 104→114，pct=(114-104)/104*100≈9.615，vol_change=0
    # → score≈5.769；BK1754: close 100→104，pct=4 → score=2.4；BK1755（1 行）不入选
    assert items[0]["sector_code"] == "BK1753"
    assert items[1]["sector_code"] == "BK1754"
    assert items[0]["sector_name"] == "光刻胶"  # sector 表名称映射
    assert items[0]["rank"] == 1 and items[1]["rank"] == 2
    assert items[0]["hotness_reason"] is None  # LLM 理由未实现恒 NULL
    assert items[0]["period_return"] is not None
    assert len(items[0]["daily_changes"]) == 10


def test_hot_concepts_single_row_degradation(client):
    """M3 三段降级之 ①：≤1 行板块热度 = 最新行 pct_chg × 0.6。"""
    _seed_sector_daily(client)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 7))
    response = client.http.get(
        "/api/v1/market-data/concepts/hot?market=CN&interval=1d&as_of=2026-09-07&limit=3"
    )
    items = response.json()["data"]["items"]
    bk1755 = next(it for it in items if it["sector_code"] == "BK1755")
    # 1 行、pct_chg=3.0 → score = 3.0 × 0.6 = 1.8（排末位但入选）
    assert bk1755["rank"] == 3
    assert bk1755["period_return"] is None  # 单行无区间收益口径


def test_hot_concepts_non_cn_returns_empty(client):
    response = client.http.get(
        "/api/v1/market-data/concepts/hot?market=US&interval=1d&as_of=2026-08-01&limit=20"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "NO_HOT_CONCEPTS"
    assert data["items"] == []




def test_concept_tree_computed_with_members(client):
    """概念树（板块概念Treemap方案 3.2）：热度排序与 heat_score、pct_chg 显式按
    as_of 取值、成员名称映射与 |pct_chg| 降序、截断 top 100 + member_total、
    停牌成员 null、板块当日缺行 null。"""
    _seed_sector_daily(client)
    _seed_concept_tree(client)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 7))
    response = client.http.get(
        "/api/v1/market-data/concepts/tree?market=CN&interval=1d&as_of=2026-09-07&limit=3"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "OK"
    assert data["source"] == "dc"
    assert data["algorithm_version"] == "heat_v1"
    assert data["freshness_status"] == "FRESH"
    items = data["items"]
    assert [it["sector_code"] for it in items] == ["BK1753", "BK1754", "BK1755"]
    # 热度排序与 heat_score（BK1753 完整公式：close 104→114，pct≈9.615 → score≈5.769）
    assert items[0]["sector_name"] == "光刻胶"
    assert items[0]["rank"] == 1
    assert items[0]["heat_score"] == pytest.approx(5.769, abs=1e-3)
    # 概念 pct_chg 显式按 as_of 取值
    assert items[0]["pct_chg"] == 1.0
    # 成员：截断 top 100 + member_total 全量数；|pct_chg| 降序
    members = items[0]["members"]
    assert items[0]["member_total"] == 103
    assert len(members) == 100
    # 600102.SH 无 instrument 行 → name 兜底 ts_code；pct 5.1102 为最大 |pct| 排头
    assert members[0] == {"ts_code": "600102.SH", "name": "600102.SH",
                          "pct_chg": pytest.approx(5.1102)}
    assert members[1]["pct_chg"] == pytest.approx(5.0101)
    assert members[1]["name"] == "成分101"
    # BK1754：停牌成员（无日线行）pct_chg null 排尾
    b1754 = items[1]["members"]
    assert [m["ts_code"] for m in b1754] == ["300001.SZ", "300002.SZ"]
    assert b1754[0]["pct_chg"] == 5.0 and b1754[0]["name"] == "成分B"
    assert b1754[1]["pct_chg"] is None and b1754[1]["name"] == "成分C"
    # BK1755：板块 as_of 当日缺行情行 → pct_chg null（不静默取更早日期）
    assert items[2]["pct_chg"] == 3.0
    assert items[2]["heat_window_rows"] == 1
    assert items[2]["member_total"] == 0


def test_concept_tree_empty_is_normal_business_state(client):
    response = client.http.get(
        "/api/v1/market-data/concepts/tree?market=CN&interval=1d&as_of=2026-08-01&limit=30"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "NO_HOT_CONCEPTS"
    assert data["items"] == []
    assert data["algorithm_version"] == "heat_v1"


def test_concept_tree_non_cn_returns_empty(client):
    response = client.http.get(
        "/api/v1/market-data/concepts/tree?market=US&interval=1d&as_of=2026-08-01&limit=30"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "NO_HOT_CONCEPTS"
    assert data["items"] == []




def test_concept_bars_returns_ohlc_ascending(client):
    """概念 K 线（板块概念Treemap方案 3.3 + m7 修订）：读 sector_daily、OHLC 脏行
    整行丢弃、指标自算（板块指数无因子源，m7 用户拍板 2026-09-16）、名称映射、
    freshness。3 根短序列：指标数组存在但与 bars 等长全 None（窗口不足即 None）。"""
    _seed_sector_daily_bars(client)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 5))
    response = client.http.get(
        "/api/v1/market-data/concepts/BK1753/bars?market=CN&source=dc&interval=1d&from=2026-09-01&to=2026-09-07"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["asset"] == {"market": "CN", "symbol": "BK1753", "name": "光刻胶"}
    assert data["interval"] == "1d"
    assert data["source"] == "dc"
    bars = data["bars"]
    assert [b["timestamp"] for b in bars] == ["2026-09-02", "2026-09-03", "2026-09-04"]
    assert bars[0]["open"] == 100.0 and bars[0]["close"] == 102.0
    assert bars[0]["high"] == 105.0 and bars[0]["low"] == 99.0
    assert bars[0]["volume"] == 1000.0
    assert bars[2]["volume"] == 1200.0
    # 涨跌幅透传 sector_daily.pct_chg 原值（升序对齐；不与 close 自算值混淆）
    assert [b["pct_chg"] for b in bars] == [2.0, 1.96, 0.96]
    # 指标自算契约（m7）：结构完整、与 bars 等长；3 根短序列窗口不足 → 全 None
    indicators = data["indicators"]
    assert indicators is not None
    assert [line["period"] for line in indicators["ma"]] == [5, 10, 20, 60]
    for line in indicators["ma"]:
        assert line["values"] == [None, None, None]
    for key in ("mid", "upper", "lower"):
        assert indicators["boll"][key] == [None, None, None]
    for key in ("dif", "dea", "hist"):
        assert indicators["macd"][key] == [None, None, None]
    assert data["freshness_status"] == "STALE"  # target-day OHLC is invalid




def test_concept_bars_compute_indicators_with_warmup(client):
    """概念指标自算（m7）：预热窗口计算（[from−120d, to] 全段 → 切回请求窗口），
    请求窗口首根指标即有值（预热生效）、与 bars 等长按 index 对齐、数值口径
    = 归档 K线指标叠加方案 3.1 / MACD指标副图方案 3.1。"""
    _seed_dense_sector_bars(client)
    from_date = date(2026, 7, 11)  # offset 10
    to_date = date(2026, 8, 9)     # offset 39
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=to_date)
    response = client.http.get(
        f"/api/v1/market-data/concepts/BK1756/bars?market=CN&source=dc&interval=1d&from={from_date}&to={to_date}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["bars"]) == 30
    indicators = data["indicators"]
    assert indicators is not None
    # 对齐：各数组与 bars 等长；窗口首根（global offset 10）预热生效即有值
    assert [line["period"] for line in indicators["ma"]] == [5, 10, 20, 60]
    for line in indicators["ma"]:
        assert len(line["values"]) == 30
    # MA5 窗口首根 = mean(closes[6..10]) = mean(106..110) = 108；
    # 末根 = mean(closes[35..39]) = mean(135..139) = 137
    assert indicators["ma"][0]["values"][0] == 108.0
    assert indicators["ma"][0]["values"][29] == 137.0
    # BOLL(20)：全局第 19 根起有值 → 窗口 index 9；窗口首根 None（窗口不足即 None）
    assert indicators["boll"]["mid"][0] is None
    assert indicators["boll"]["mid"][9] == 109.5  # MA20(global 19) = mean(100..119)
    # 末根 mid = mean(120..139) = 129.5；σ 总体标准差 ddof=0（归档方案 D6）：
    # 窗口 [120..139] 偏差 ±9.5..±0.5 步长 1，方差 = 33.25 → σ = √33.25 ≈ 5.7663
    assert indicators["boll"]["mid"][29] == 129.5
    assert indicators["boll"]["upper"][29] == pytest.approx(129.5 + 2.0 * 33.25 ** 0.5)
    assert indicators["boll"]["lower"][29] == pytest.approx(129.5 - 2.0 * 33.25 ** 0.5)
    # MACD 边界：dif 第 slow−1=25 根（global）起有值 → 窗口 index 15；
    # dea 再滞后 signal−1=8 根 → 窗口 index 23
    macd = indicators["macd"]
    assert (macd["fast"], macd["slow"], macd["signal"]) == (12, 26, 9)
    assert macd["dif"][14] is None and macd["dif"][15] is not None
    assert macd["dea"][22] is None and macd["dea"][23] is not None
    # hist = 2×(dif−dea)（末根一致性，国内惯例 2×）
    assert macd["hist"][29] == pytest.approx(2.0 * (macd["dif"][29] - macd["dea"][29]))
    # MA60：40 根全历史不足 60 → 全 None（数据缺失是事实，不伪造）
    assert indicators["ma"][3]["values"] == [None] * 30


def test_concept_bars_errors(client):
    # 未知板块 → 404
    response = client.http.get(
        "/api/v1/market-data/concepts/BK9999/bars?market=CN&source=dc&interval=1d&from=2026-09-01&to=2026-09-07"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    # interval 非白名单 → 422（CR 二轮补：与 tree/hot/indices 同款语义）
    response = client.http.get(
        "/api/v1/market-data/concepts/BK9999/bars?market=CN&source=dc&interval=5d&from=2026-09-01&to=2026-09-07"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INTERVAL_NOT_SUPPORTED"
    # from>to → 422
    _seed_sector_daily_bars(client)
    response = client.http.get(
        "/api/v1/market-data/concepts/BK1753/bars?market=CN&source=dc&interval=1d&from=2026-09-07&to=2026-09-01"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "RANGE_TOO_LARGE"




def test_stock_bars_returns_pure_kline(client):
    """个股 K 线（板块概念Treemap方案 3.3 + m7 修订）：get_bars 放宽 stock、因子表
    无行且按需拉取无数据 → indicators=None 纯 K 线降级（拉取不阻断 K 线响应；
    与指数路径全 null 数组降级语义分流）。"""
    _seed_stock_bars(client)
    # 测试注入 fake 拉取器（None = 上游无数据），防真实 tushare 网络调用
    client.http.app.state.stock_factor_fetcher = lambda symbol, start, end: None
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/stocks/600519.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-07"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["asset"] == {"market": "CN", "symbol": "600519.SH", "name": "贵州茅台"}
    assert [b["close"] for b in data["bars"]] == [1500.0, 1501.0]
    assert [b["pct_chg"] for b in data["bars"]] == [0.5, 1.5]  # 上游原值透传
    assert data["indicators"] is None  # 拉取无数据 → 纯 K 线
    assert data["freshness_status"] == "FRESH"


def test_stock_bars_fetches_stk_factors_on_demand_and_caches(client):
    """个股因子按需拉取（m7 用户拍板 2026-09-16：个股接因子、概念自算）：因子表
    无行 → 调 stk_factor_pro（注入 fake）→ 入库 factor_daily（缓存层）→ 指标按
    因子值透传；第二次同区间请求走缓存不再调上游。"""
    _seed_stock_bars(client)
    import pandas as pd

    calls = []

    def fake_fetcher(symbol, start, end):
        calls.append((symbol, start, end))
        return pd.DataFrame({
            "trade_date": ["2026-09-03", "2026-09-04"],
            "ma_bfq_5": [1510.0, 1511.0],
            "ma_bfq_10": [1520.0, 1521.0],
            "ma_bfq_20": [1530.0, 1531.0],
            "ma_bfq_60": [1560.0, 1561.0],
            "boll_mid_bfq": [1502.0, 1503.0],
            "boll_upper_bfq": [1530.0, 1531.0],
            "boll_lower_bfq": [1470.0, 1471.0],
            "macd_dif_bfq": [0.5, 0.6],
            "macd_dea_bfq": [0.3, 0.4],
            "macd_bfq": [0.4, 0.5],
        })

    client.http.app.state.stock_factor_fetcher = fake_fetcher
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    url = (
        "/api/v1/market-data/stocks/600519.SH/bars?market=CN&interval=1d"
        "&from=2026-09-01&to=2026-09-07"
    )

    response = client.http.get(url)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["asset"] == {"market": "CN", "symbol": "600519.SH", "name": "贵州茅台"}
    indicators = data["indicators"]
    assert indicators is not None
    # 指标逐值透传拉取到的因子（不自算）
    assert [line["period"] for line in indicators["ma"]] == [5, 10, 20, 60]
    assert indicators["ma"][0]["values"] == [1510.0, 1511.0]
    assert indicators["ma"][3]["values"] == [1560.0, 1561.0]
    assert indicators["boll"]["mid"] == [1502.0, 1503.0]
    assert indicators["boll"]["upper"] == [1530.0, 1531.0]
    assert indicators["macd"]["hist"] == [0.4, 0.5]
    # 按请求区间调用一次（fake 收到的 from/to 与请求一致）
    assert calls == [("600519.SH", "2026-09-01", "2026-09-07")]

    # 因子已入库缓存：第二次同区间请求不再调上游、指标仍产出（表内读）
    response = client.http.get(url)
    assert response.status_code == 200
    assert response.json()["data"]["indicators"]["ma"][0]["values"] == [1510.0, 1511.0]
    assert len(calls) == 1


def test_stock_bars_refetches_when_factor_cache_is_daily_residue(client):
    """因子缓存残段不挡历史补拉（2026-09-21 实况修复）：每日增量只写最新 1~2 天
    个股因子，残段落在请求窗口内但远未覆盖 from 侧 → 仍按需拉取全区间补齐，
    指标为拉取值（而非以残段对齐 bars、其余全 None 的画不出均线态）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    _seed_stock_bars(client)
    # seed 每日增量残段：窗口内仅 1 行因子（最新一天），历史侧为空
    engine = create_engine(_test_db_url(CoreSettings().resolved_database_url()))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO market.factor_daily (ts_code, trade_date, ma_bfq_5) "
                "VALUES ('600519.SH', '2026-09-04', 1490.0) "
                "ON CONFLICT (ts_code, trade_date) DO NOTHING"
            ),
        )

    import pandas as pd

    calls = []

    def fake_fetcher(symbol, start, end):
        calls.append((symbol, start, end))
        return pd.DataFrame({
            "trade_date": ["2026-09-03", "2026-09-04"],
            "ma_bfq_5": [1510.0, 1511.0],
            "ma_bfq_10": [1520.0, 1521.0],
            "ma_bfq_20": [1530.0, 1531.0],
            "ma_bfq_60": [1560.0, 1561.0],
            "boll_mid_bfq": [1502.0, 1503.0],
            "boll_upper_bfq": [1530.0, 1531.0],
            "boll_lower_bfq": [1470.0, 1471.0],
            "macd_dif_bfq": [0.5, 0.6],
            "macd_dea_bfq": [0.3, 0.4],
            "macd_bfq": [0.4, 0.5],
        })

    client.http.app.state.stock_factor_fetcher = fake_fetcher
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/stocks/600519.SH/bars?market=CN&interval=1d&from=2026-03-20&to=2026-09-07"
    )
    assert response.status_code == 200
    # 实际 bars 有两天、缓存只覆盖一天 → 精确交易日校验触发全区间补拉
    assert calls == [("600519.SH", "2026-03-20", "2026-09-07")]
    indicators = response.json()["data"]["indicators"]
    assert indicators is not None
    assert indicators["ma"][0]["values"] == [1510.0, 1511.0]


def test_stock_bars_rejects_non_stock_index(client):
    # fund 代码 → 404（instrument_type 白名单 index/stock）
    _seed_stock_bars(client)
    response = client.http.get(
        "/api/v1/market-data/stocks/510300.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-07"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    # 不存在 → 404
    response = client.http.get(
        "/api/v1/market-data/stocks/999999.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-07"
    )
    assert response.status_code == 404


def test_trends_endpoints_contract(client):
    """趋势端点契约（趋势对比面板方案 4.2.3）：2 端点 200 响应形状（序列条目录
    保留、空窗口 points=[]、as_of/freshness）+ from>to → 422 RANGE_TOO_LARGE。"""
    _seed_bars(client, "000300.SH", trade_date=date(2026, 9, 4), close=3340.0)
    _seed_bars(client, "932000.CSI", trade_date=date(2026, 9, 4), close=2300.0)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/trends/cap-tiers?from=2026-09-01&to=2026-09-05")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["from"] == "2026-09-01" and data["to"] == "2026-09-05"
    series = data["series"]
    assert [s["symbol"] for s in series] == ["000300.SH", "000905.SH", "000852.SH", "932000.CSI"]
    assert [s["name"] for s in series] == ["沪深300", "中证500", "中证1000", "中证2000"]
    assert series[0]["points"] == [{"date": "2026-09-04", "close": 3340.0}]
    assert series[1]["points"] == []   # 未 seed → 空窗口契约（序列条目保留）
    assert data["as_of"] == "2026-09-04"
    assert data["freshness_status"] == "STALE"  # two peer indexes have no target-day rows

    # boards 端点：板组未 seed → 三序列条目保留、points 全空
    response = client.http.get(
        "/api/v1/market-data/trends/boards?from=2026-09-01&to=2026-09-05")
    assert response.status_code == 200
    boards = response.json()["data"]["series"]
    assert [s["symbol"] for s in boards] == ["000001.SH", "399006.SZ", "000688.SH"]
    assert [s["name"] for s in boards] == ["上证综指", "创业板指", "科创50"]
    assert all(s["points"] == [] for s in boards)

    # from>to → 422 + RANGE_TOO_LARGE（照抄既有断言形态）
    response = client.http.get(
        "/api/v1/market-data/trends/cap-tiers?from=2026-09-05&to=2026-09-01")
    assert response.status_code == 422
    assert response.json()["code"] == "RANGE_TOO_LARGE"

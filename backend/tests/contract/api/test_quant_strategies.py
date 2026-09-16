"""量化策略契约测试（plan 4.1.1/4.4.1：端点/DTO/状态机/错误码/源码可见性原则）。"""

from __future__ import annotations

LEGAL = '''
def strategy(context):
    close = context["ohlcv"]["close"]
    ma5 = context["indicators"]["ma_bfq_5"][-1]
    price = close[-1]
    if price > ma5:
        return {"action": "BUY", "score": 90, "entry_price": price,
                "stop_loss": round(price * 0.965, 2), "take_profit": round(price * 1.10, 2),
                "sell_ratio": None, "reason": "MA5 上穿"}
    return {"action": "HOLD", "score": 50, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "sell_ratio": None, "reason": "不满足"}
'''

_SENTINEL = "def strategy(context):\n    import os\n"


def _create(client, name: str, source_code: str = "") -> dict:
    response = client.http.post("/api/v1/quant-strategies", json={"name": name, "source_code": source_code})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_strategy_crud_publish_archive_flow(client):
    created = _create(client, "契约策略", source_code=LEGAL)
    assert created["name"] == "契约策略"
    assert len(created["versions"]) == 1
    assert created["versions"][0]["status"] == "DRAFT"
    # 列表/详情 DTO 永不含源码
    assert "source_code" not in created
    assert "source_code" not in created["versions"][0]

    sid = created["id"]
    draft = created["versions"][0]

    # 草稿读取：唯一可返回源码的接口
    got_draft = client.http.get(f"/api/v1/quant-strategies/{sid}/draft")
    assert got_draft.status_code == 200
    assert got_draft.json()["data"]["source_code"] == LEGAL

    # 发布 → PUBLISHED + 下一版 DRAFT
    published = client.http.post(
        f"/api/v1/quant-strategies/{sid}/versions/{draft['id']}/publish",
        json={"expected_version": draft["version"]},
    )
    assert published.status_code == 200
    assert published.json()["data"]["published"]["status"] == "PUBLISHED"
    assert published.json()["data"]["published"]["published_at"] is not None
    next_draft = published.json()["data"]["next_draft"]
    assert next_draft["status"] == "DRAFT"
    assert next_draft["version_no"] == 2

    # 发布 PUBLISHED → 非法状态
    bad_publish = client.http.post(
        f"/api/v1/quant-strategies/{sid}/versions/{draft['id']}/publish",
        json={"expected_version": published.json()["data"]["published"]["version"]},
    )
    assert bad_publish.status_code == 409
    assert bad_publish.json()["code"] == "STRATEGY_VERSION_INVALID_STATE"

    # 归档唯一 PUBLISHED → 拒绝（至少保留一个）
    only_archive = client.http.post(f"/api/v1/quant-strategies/{sid}/versions/{draft['id']}/archive")
    assert only_archive.status_code == 409
    assert only_archive.json()["code"] == "STRATEGY_VERSION_INVALID_STATE"


def test_draft_save_validation_422_and_source_not_echoed(client):
    created = _create(client, "校验策略")
    sid = created["id"]
    draft = created["versions"][0]
    bad = client.http.put(
        f"/api/v1/quant-strategies/{sid}/draft",
        json={
            "name": "校验策略",
            "description": None,
            "source_code": _SENTINEL,
            "expected_strategy_version": 1,
            "expected_draft_version": draft["version"],
        },
    )
    assert bad.status_code == 422
    body = bad.json()
    assert body["code"] == "STRATEGY_VALIDATION_FAILED"
    # detail 含稳定问题码与位置，不含客户端提交的源码文本
    assert "FORBIDDEN_IMPORT" in body["detail"]
    assert "import os" not in body["detail"]
    # 校验失败不落库
    still = client.http.get(f"/api/v1/quant-strategies/{sid}/draft")
    assert still.json()["data"]["source_code"] == ""


def test_revision_conflicts(client):
    created = _create(client, "并发策略")
    sid = created["id"]
    draft = created["versions"][0]
    ok = client.http.put(
        f"/api/v1/quant-strategies/{sid}/draft",
        json={
            "name": "并发策略",
            "description": None,
            "source_code": LEGAL,
            "expected_strategy_version": 1,
            "expected_draft_version": draft["version"],
        },
    )
    assert ok.status_code == 200
    stale = client.http.put(
        f"/api/v1/quant-strategies/{sid}/draft",
        json={
            "name": "并发策略",
            "description": None,
            "source_code": LEGAL,
            "expected_strategy_version": 1,
            "expected_draft_version": draft["version"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "STRATEGY_REVISION_CONFLICT"


def test_not_found_codes(client):
    missing_id = "00000000-0000-0000-0000-000000000000"
    assert client.http.get(f"/api/v1/quant-strategies/{missing_id}").status_code == 404
    assert client.http.get(f"/api/v1/quant-strategies/{missing_id}").json()["code"] == "STRATEGY_NOT_FOUND"
    assert client.http.get(f"/api/v1/quant-strategies/{missing_id}/draft").status_code == 404


def test_portfolio_account_fields_roundtrip(client):
    created = client.http.post(
        "/api/v1/portfolios",
        json={
            "name": "账户组合",
            "total_assets": 100000,
            "available_cash": 35000,
            "risk_per_trade_pct": 0.01,
            "min_risk_reward_ratio": 2,
            "max_total_position_pct": 0.8,
            "max_single_stock_pct": 0.1,
            "max_sector_pct": 0.3,
        },
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["total_assets"] == 100000
    assert data["available_cash"] == 35000
    assert data["risk_per_trade_pct"] == 0.01
    assert data["max_total_position_pct"] == 0.8

    pid = data["id"]
    # 原子 PATCH：全部账户字段 + expected_version
    patched = client.http.patch(
        f"/api/v1/portfolios/{pid}",
        json={
            "name": "账户组合-改",
            "total_assets": 200000,
            "available_cash": 50000,
            "risk_per_trade_pct": 0.02,
            "min_risk_reward_ratio": 2.5,
            "max_total_position_pct": 0.7,
            "max_single_stock_pct": 0.15,
            "max_sector_pct": 0.25,
            "expected_version": 1,
        },
    )
    assert patched.status_code == 200, patched.text
    d2 = patched.json()["data"]
    assert d2["name"] == "账户组合-改"
    assert d2["total_assets"] == 200000
    assert d2["version"] == 2

    # 跨字段校验：现金 > 总资产 → 422
    bad = client.http.patch(
        f"/api/v1/portfolios/{pid}",
        json={
            "name": "账户组合-改",
            "total_assets": 1000,
            "available_cash": 2000,
            "risk_per_trade_pct": 0.02,
            "min_risk_reward_ratio": 2.5,
            "max_total_position_pct": 0.7,
            "max_single_stock_pct": 0.15,
            "max_sector_pct": 0.25,
            "expected_version": 2,
        },
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "PORTFOLIO_ACCOUNT_INVALID"

    # 单票上限 > 总仓位上限 → 422
    bad2 = client.http.patch(
        f"/api/v1/portfolios/{pid}",
        json={
            "name": "账户组合-改",
            "total_assets": 200000,
            "available_cash": 50000,
            "risk_per_trade_pct": 0.02,
            "min_risk_reward_ratio": 2.5,
            "max_total_position_pct": 0.2,
            "max_single_stock_pct": 0.5,
            "max_sector_pct": 0.25,
            "expected_version": 2,
        },
    )
    assert bad2.status_code == 422
    assert bad2.json()["code"] == "PORTFOLIO_ACCOUNT_INVALID"

    # 过期版本 → 409 可重拉（当前 version=2，用旧版本号 1 触发冲突）
    stale = client.http.patch(
        f"/api/v1/portfolios/{pid}",
        json={
            "name": "账户组合-改",
            "total_assets": 200000,
            "available_cash": 50000,
            "risk_per_trade_pct": 0.02,
            "min_risk_reward_ratio": 2.5,
            "max_total_position_pct": 0.7,
            "max_single_stock_pct": 0.15,
            "max_sector_pct": 0.25,
            "expected_version": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    # 默认参数创建：不传账户字段 → 0 + 默认值
    defaulted = client.http.post("/api/v1/portfolios", json={"name": "默认组合"})
    assert defaulted.status_code == 201
    d3 = defaulted.json()["data"]
    assert d3["total_assets"] == 0
    assert d3["available_cash"] == 0
    assert d3["risk_per_trade_pct"] == 0.01
    assert d3["min_risk_reward_ratio"] == 2
    assert d3["max_total_position_pct"] == 0.8
    assert d3["max_single_stock_pct"] == 0.1
    assert d3["max_sector_pct"] == 0.3


def test_format_endpoint(client):
    resp = client.http.post(
        "/api/v1/quant-strategies/format",
        json={"source_code": "def strategy(context):\n    x=1\n    return None\n"},
    )
    assert resp.status_code == 200, resp.text
    formatted = resp.json()["data"]["source_code"]
    assert "x = 1" in formatted

    # 语法错误 → 422 且 detail 不含客户端源码
    bad = client.http.post(
        "/api/v1/quant-strategies/format",
        json={"source_code": "def strategy(context:\n    pass\n"},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "STRATEGY_FORMAT_FAILED"

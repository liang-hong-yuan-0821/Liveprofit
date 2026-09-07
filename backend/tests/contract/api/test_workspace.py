"""自选/组合契约测试（§2.6.3：path/query/body/DTO、排序、revision、错误码）。"""

from __future__ import annotations


def _create_watchlist(client, name: str) -> dict:
    response = client.http.post("/api/v1/watchlists", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _add_item(client, watchlist_id: str, market: str, symbol: str, revision: int):
    response = client.http.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        json={"market": market, "symbol": symbol, "expected_watchlist_revision": revision},
    )
    return response


def test_watchlist_crud_and_conflicts(client):
    created = _create_watchlist(client, "分组1")
    assert set(created.keys()) == {"id", "name", "version", "item_count", "created_at", "updated_at"}
    assert created["version"] == 1

    # 重名冲突
    conflict = client.http.post("/api/v1/watchlists", json={"name": "分组1"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "WATCHLIST_NAME_CONFLICT"

    # 改名（version 并发）
    renamed = client.http.patch(
        f"/api/v1/watchlists/{created['id']}", json={"name": "分组1-改", "expected_version": 1}
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["version"] == 2
    stale = client.http.patch(
        f"/api/v1/watchlists/{created['id']}", json={"name": "分组1-再改", "expected_version": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    # 空分组删除（200 + envelope）；契约无单分组 GET，删除后经列表确认不存在
    deleted = client.http.delete(f"/api/v1/watchlists/{created['id']}?expected_version=2")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True, "resource_id": created["id"]}
    remaining = client.http.get("/api/v1/watchlists?limit=100")
    assert created["id"] not in {i["id"] for i in remaining.json()["data"]["items"]}


def test_watchlist_items_order_and_not_empty(client):
    created = _create_watchlist(client, "分组2")
    r1 = _add_item(client, created["id"], "CN", "000001.SH", 1)
    assert r1.status_code == 201
    assert r1.json()["data"]["watchlist_revision"] == 2
    r2 = _add_item(client, created["id"], "US", "AAPL", 2)
    assert r2.status_code == 201

    dup = _add_item(client, created["id"], "CN", "000001.SH", 3)
    assert dup.status_code == 409
    assert dup.json()["code"] == "WATCHLIST_ITEM_DUPLICATE"

    # 非空删除
    not_empty = client.http.delete(f"/api/v1/watchlists/{created['id']}?expected_version=3")
    assert not_empty.status_code == 409
    assert not_empty.json()["code"] == "WATCHLIST_NOT_EMPTY"

    # 批量排序：错 revision 与错集合均 409；正确整体重排
    bad_revision = client.http.put(
        f"/api/v1/watchlists/{created['id']}/items/order",
        json={
            "expected_watchlist_revision": 2,
            "items": [{"market": "US", "symbol": "AAPL", "display_order": 0},
                      {"market": "CN", "symbol": "000001.SH", "display_order": 1}],
        },
    )
    assert bad_revision.status_code == 409
    assert bad_revision.json()["code"] == "WATCHLIST_ITEM_ORDER_CONFLICT"

    bad_set = client.http.put(
        f"/api/v1/watchlists/{created['id']}/items/order",
        json={"expected_watchlist_revision": 3, "items": [{"market": "US", "symbol": "AAPL", "display_order": 0}]},
    )
    assert bad_set.status_code == 409

    reordered = client.http.put(
        f"/api/v1/watchlists/{created['id']}/items/order",
        json={
            "expected_watchlist_revision": 3,
            "items": [{"market": "US", "symbol": "AAPL", "display_order": 0},
                      {"market": "CN", "symbol": "000001.SH", "display_order": 1}],
        },
    )
    assert reordered.status_code == 200
    data = reordered.json()["data"]
    assert data["watchlist_revision"] == 4
    assert [(i["market"], i["symbol"], i["display_order"]) for i in data["items"]] == [
        ("US", "AAPL", 0), ("CN", "000001.SH", 1),
    ]

    # 标的删除（200 + envelope）
    item_id = data["items"][0]["id"]
    removed = client.http.delete(
        f"/api/v1/watchlists/{created['id']}/items/{item_id}?expected_watchlist_revision=4"
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["deleted"] is True


def test_watchlist_list_sorting_and_cursor(client):
    for i in range(3):
        _create_watchlist(client, f"分页组-{i}")
    page1 = client.http.get("/api/v1/watchlists?limit=2")
    assert page1.status_code == 200
    assert len(page1.json()["data"]["items"]) == 2
    cursor = page1.json()["meta"]["next_cursor"]
    assert cursor is not None
    page2 = client.http.get(f"/api/v1/watchlists?limit=2&cursor={cursor}")
    assert page2.status_code == 200
    ids1 = {i["id"] for i in page1.json()["data"]["items"]}
    ids2 = {i["id"] for i in page2.json()["data"]["items"]}
    assert ids1.isdisjoint(ids2)
    assert page2.json()["meta"]["next_cursor"] is None


def test_portfolio_positions_validation_and_conflicts(client):
    created = client.http.post("/api/v1/portfolios", json={"name": "组合1"})
    assert created.status_code == 201
    portfolio = created.json()["data"]

    # INVALID_POSITION
    invalid = client.http.put(
        f"/api/v1/portfolios/{portfolio['id']}/positions/CN/000001.SH",
        json={"quantity": 0, "average_cost": 10, "expected_portfolio_revision": 1},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_POSITION"

    upserted = client.http.put(
        f"/api/v1/portfolios/{portfolio['id']}/positions/CN/000001.SH",
        json={"quantity": 100, "average_cost": 12.5, "expected_portfolio_revision": 1},
    )
    assert upserted.status_code == 200
    data = upserted.json()["data"]
    assert data["position"]["quantity"] == 100
    assert data["portfolio_revision"] == 2

    # revision 冲突
    conflict = client.http.put(
        f"/api/v1/portfolios/{portfolio['id']}/positions/CN/000001.SH",
        json={"quantity": 200, "average_cost": 13, "expected_portfolio_revision": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "PORTFOLIO_POSITION_CONFLICT"

    # 非空组合删除
    not_empty = client.http.delete(f"/api/v1/portfolios/{portfolio['id']}?expected_version=2")
    assert not_empty.status_code == 409
    assert not_empty.json()["code"] == "PORTFOLIO_NOT_EMPTY"

    # positions 列表排序（market ASC, symbol ASC）
    client.http.put(
        f"/api/v1/portfolios/{portfolio['id']}/positions/US/AAPL",
        json={"quantity": 1, "average_cost": 200, "expected_portfolio_revision": 2},
    )
    positions = client.http.get(f"/api/v1/portfolios/{portfolio['id']}/positions")
    assert positions.status_code == 200
    items = positions.json()["data"]["items"]
    assert [(i["market"], i["symbol"]) for i in items] == [("CN", "000001.SH"), ("US", "AAPL")]

    # 删除持仓 + 空组合删除
    removed = client.http.delete(
        f"/api/v1/portfolios/{portfolio['id']}/positions/CN/000001.SH?expected_portfolio_revision=3"
    )
    assert removed.status_code == 200
    # resource_id 指向被删持仓自身（复合键），而非父组合
    assert removed.json()["data"]["resource_id"] == "CN:000001.SH"
    client.http.delete(
        f"/api/v1/portfolios/{portfolio['id']}/positions/US/AAPL?expected_portfolio_revision=4"
    )
    deleted = client.http.delete(f"/api/v1/portfolios/{portfolio['id']}?expected_version=5")
    assert deleted.status_code == 200

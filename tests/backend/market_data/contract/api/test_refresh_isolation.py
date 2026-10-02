# test-catalog-begin
# {
#   "purpose": "行情服务 / refresh_isolation（行情刷新、隔离）",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "行情刷新",
#     "状态",
#     "refresh_isolation",
#     "refresh",
#     "status"
#   ],
#   "covers": [
#     "tests/support/python/contract_env.py",
#     "tests/support/python/market_refresh_support.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

import pytest


@pytest.mark.parametrize("case", ["write_then_clear", "reverse_order"])
def test_trade_status_fixture_and_fake_publisher(client, case):
    with client.http.app.state.market_conn() as conn:
        assert conn.execute("SELECT current_database()").fetchone()[0] == "liveprofit_contract_test"
        assert conn.execute("SELECT count(*) FROM market.trade_status_daily").fetchone()[0] == 0
        conn.execute("INSERT INTO market.trade_status_daily "
                     "(ts_code,trade_date,is_suspended,source) "
                     "VALUES ('600000.SH','2026-09-22',true,'test')")
        conn.commit()
    assert client.redis.connection_pool.connection_kwargs["db"] == 11
    assert not client.publisher.job_ids
    client.http.app.state.market_refresh_publisher.send("fixture-only")
    assert client.publisher.job_ids == ["fixture-only"]

# test-catalog-begin
# {
#   "purpose": "量化信号 cursor 与报告投影契约测试（plan 4.3.1/4.4.1）。",
#   "keywords": [
#     "量化策略",
#     "执行",
#     "订单",
#     "交易信号",
#     "quant_signals",
#     "execution",
#     "order",
#     "signals"
#   ],
#   "covers": [
#     "backend/api/routers/quant_signals.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

"""量化信号 cursor 与报告投影契约测试（plan 4.3.1/4.4.1）。"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import create_engine, text

from tests.support.python.contract_env import _base_db_url, _test_db_url


def _engine():
    return create_engine(_test_db_url(_base_db_url()))


def _seed_quant_task_with_signals():
    """直连契约库播种：任务+报告（decision.quant_execution）+ 两个 attempt 的信号行。"""
    task_id = uuid.uuid4()
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_tasks (id, task_type, status, request_params, selected_layers, input_hash, attempt_no, "
                "started_at, finished_at) VALUES (:id, 'MARKET_WIDE', 'SUCCEEDED', '{}'::jsonb, '[\"position\"]'::jsonb, 'h', 1, now(), now())"
            ),
            {"id": task_id},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_reports (id, task_id, attempt_no, report_version, schema_version, report_json, "
                "risk_flag, has_report, decision, generated_at) VALUES "
                "(:rid, :tid, 1, 1, 'v1', '{\"sections\": []}'::jsonb, false, true, CAST(:decision AS JSONB), now())"
            ),
            {
                "rid": uuid.uuid4(),
                "tid": task_id,
                "decision": json.dumps(
                    {
                        "quant_execution": {
                            "strategy": {"name": "契约策略", "version_no": 1, "source_hash_prefix": "abcdef123456"},
                            "portfolio_snapshot": {"name": "核心仓", "version": 1, "total_assets": "100000.0000", "available_cash": "35000.0000", "risk": {}},
                            "summary": {"universe_total": 3, "data_complete": 3, "scanned": 3, "buy_matches": 3,
                                        "suggested_buy_orders": 2, "suggested_sell_orders": 1, "failed_count": 0},
                            "warnings": ["AI 风险门控未接入；组合开放风险与熔断已启用"],
                            "valued_at": "2026-09-16T14:00:00+00:00",
                            "requested_trade_date": "2026-09-16",
                            "market_as_of_trade_date": "2026-09-15",
                        }
                    },
                    ensure_ascii=False,
                ),
            },
        )
        # attempt 1 信号：3 BUY（分数降序 90/80/70，两单已获订单）、1 持仓、2 错误
        rows = [
            ("BUY", "000003.SZ", 70, None),
            ("BUY", "000001.SZ", 90, "ELIGIBLE"),
            ("BUY", "000002.SZ", 80, "ELIGIBLE"),
            ("HOLDING", "600519.SH", None, "ELIGIBLE"),
            ("ERROR", "300001.SZ", None, None),
            ("ERROR", "300002.SZ", None, None),
        ]
        for kind, ts, score, order_status in rows:
            conn.execute(
                text(
                    "INSERT INTO quant_execution_signals (task_id, attempt_no, signal_kind, ts_code, action, score, reason, "
                    "entry_price, stop_loss, take_profit, order_status, shares, notional, error_code) VALUES "
                    "(:tid, 1, :kind, :ts, :action, :score, 'r', 10, 9, 12, :os, 100, 1000, :err)"
                ),
                {
                    "tid": task_id,
                    "kind": kind,
                    "ts": ts,
                    "action": "BUY" if kind == "BUY" else ("SELL_ALL" if kind == "HOLDING" else None),
                    "score": score,
                    "os": order_status,
                    "err": "EXECUTION_TIMEOUT" if kind == "ERROR" else None,
                },
            )
        # attempt 2：1 条 BUY（隔离验证）
        conn.execute(
            text(
                "INSERT INTO quant_execution_signals (task_id, attempt_no, signal_kind, ts_code, action, score, reason, "
                "entry_price, stop_loss, take_profit, error_code) VALUES "
                "(:tid, 2, 'BUY', '999999.SZ', 'BUY', 99, 'r2', 10, 9, 12, NULL)"
            ),
            {"tid": task_id},
        )
    engine.dispose()
    return task_id


def test_report_projection_and_buy_preview_order(client):
    task_id = _seed_quant_task_with_signals()
    resp = client.http.get(f"/api/v1/analysis-tasks/{task_id}/report")
    assert resp.status_code == 200, resp.text
    qe = resp.json()["data"]["quant_execution"]
    assert qe is not None
    assert qe["strategy"]["source_hash_prefix"] == "abcdef123456"
    assert qe["summary"]["universe_total"] == 3
    assert "组合开放风险与熔断已启用" in qe["warnings"][0]
    assert qe["requested_trade_date"] == "2026-09-16"
    assert qe["market_as_of_trade_date"] == "2026-09-15"
    # BUY 预览按 score 降序、symbol 升序
    buys = qe["matching_buy_preview"]
    assert [b["symbol"] for b in buys] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert buys[0]["order_status"] == "ELIGIBLE"
    # 持仓信号与订单预览
    assert [h["symbol"] for h in qe["holding_signal_preview"]] == ["600519.SH"]
    assert {o["symbol"] for o in qe["suggested_order_preview"]} == {"000001.SZ", "000002.SZ", "600519.SH"}


def test_buy_cursor_pagination_no_duplicates(client):
    task_id = _seed_quant_task_with_signals()
    base = f"/api/v1/analysis-tasks/{task_id}/quant-signals"
    page1 = client.http.get(f"{base}?kind=buy&limit=2")
    assert page1.status_code == 200, page1.text
    data = page1.json()["data"]
    assert data["attempt_no"] == 1
    assert [i["ts_code"] for i in data["items"]] == ["000001.SZ", "000002.SZ"]
    assert data["next_cursor"] is not None

    page2 = client.http.get(f"{base}?kind=buy&limit=2&attempt_no=1&cursor={data['next_cursor']}")
    assert page2.status_code == 200
    items2 = page2.json()["data"]["items"]
    assert [i["ts_code"] for i in items2] == ["000003.SZ"]
    assert page2.json()["data"]["next_cursor"] is None


def test_cursor_kind_and_attempt_mismatch_rejected(client):
    task_id = _seed_quant_task_with_signals()
    base = f"/api/v1/analysis-tasks/{task_id}/quant-signals"
    page = client.http.get(f"{base}?kind=buy&limit=1")
    cursor = page.json()["data"]["next_cursor"]
    # kind 不一致 → 422
    bad_kind = client.http.get(f"{base}?kind=holding&limit=2&attempt_no=1&cursor={cursor}")
    assert bad_kind.status_code == 422
    # attempt 不一致 → 422
    bad_attempt = client.http.get(f"{base}?kind=buy&limit=2&attempt_no=2&cursor={cursor}")
    assert bad_attempt.status_code == 422
    # 不带 attempt_no 的 cursor 请求 → 422
    no_attempt = client.http.get(f"{base}?kind=buy&limit=2&cursor={cursor}")
    assert no_attempt.status_code == 422


def test_attempt_isolation_and_errors_kind(client):
    task_id = _seed_quant_task_with_signals()
    base = f"/api/v1/analysis-tasks/{task_id}/quant-signals"
    # 显式 attempt 2：只有 999999.SZ
    page = client.http.get(f"{base}?kind=buy&attempt_no=2")
    assert [i["ts_code"] for i in page.json()["data"]["items"]] == ["999999.SZ"]
    # errors：ts_code 升序
    err = client.http.get(f"{base}?kind=errors&limit=10")
    assert [i["ts_code"] for i in err.json()["data"]["items"]] == ["300001.SZ", "300002.SZ"]
    assert all(i["error_code"] == "EXECUTION_TIMEOUT" for i in err.json()["data"]["items"])


def test_old_report_quant_execution_null(client):
    # 无 decision 的旧报告 → quant_execution 为 null（不渲染面板）
    task_id = uuid.uuid4()
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_tasks (id, task_type, status, request_params, selected_layers, input_hash, attempt_no) "
                "VALUES (:id, 'MARKET_WIDE', 'SUCCEEDED', '{}'::jsonb, '[\"market\"]'::jsonb, 'h', 1)"
            ),
            {"id": task_id},
        )
        conn.execute(
            text(
                "INSERT INTO analysis_reports (id, task_id, attempt_no, report_version, schema_version, report_json, "
                "risk_flag, has_report, generated_at) VALUES "
                "(:rid, :tid, 1, 1, 'v1', '{\"sections\": []}'::jsonb, false, true, now())"
            ),
            {"rid": uuid.uuid4(), "tid": task_id},
        )
    engine.dispose()
    resp = client.http.get(f"/api/v1/analysis-tasks/{task_id}/report")
    assert resp.status_code == 200
    assert resp.json()["data"]["quant_execution"] is None

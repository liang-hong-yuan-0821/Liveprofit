"""market_ingest thin wrapper 参数等价测试（方案 3.4.3）。

- 五 flag 全集保留：--start/--end/--days/--market/--skip-bars
- --start 或 --skip-bars → backfill 模式（--skip-bars 映射内部 skip_daily=True）
- 默认（无 --start）→ incremental 模式
- --market 保留兼容、不参与门控（2026-09-14 US/KR 上线后采集范围由
  INDEX_TARGETS 决定；任意值正常进入对应模式）
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.workers import market_ingest


def test_backfill_mode_with_explicit_start(monkeypatch):
    run_backfill = MagicMock(return_value={"index": {"bars": 1, "factors": 1},
                                           "days_done": 0, "failed_days": []})
    with patch("db.instrument.ingest.backfill.run_backfill", run_backfill), \
         patch("backend.workers.market_ingest.get_connection") as gc:
        gc.return_value.__enter__.return_value = "conn"
        rc = market_ingest.main(["--start", "2026-01-01", "--end", "2026-01-31"])
    assert rc == 0
    args, kwargs = run_backfill.call_args
    assert args[0] == "conn"
    assert args[1] == "2026-01-01" and args[2] == "2026-01-31"
    assert kwargs["skip_daily"] is False
    assert callable(kwargs["provider_factory"])


def test_skip_bars_maps_to_skip_daily(monkeypatch):
    run_backfill = MagicMock(return_value={"index": {"bars": 0, "factors": 5},
                                           "days_done": 0, "failed_days": []})
    with patch("db.instrument.ingest.backfill.run_backfill", run_backfill), \
         patch("backend.workers.market_ingest.get_connection") as gc:
        gc.return_value.__enter__.return_value = "conn"
        rc = market_ingest.main(["--skip-bars"])
    assert rc == 0
    # --skip-bars → backfill 模式 + skip_daily=True（仅指数日线+因子）
    assert run_backfill.call_args.kwargs["skip_daily"] is True


def test_default_mode_is_incremental(monkeypatch):
    collect = MagicMock(return_value={"daily": {}})
    with patch("db.instrument.ingest.incremental.collect_incremental", collect), \
         patch("backend.workers.market_ingest.get_connection") as gc:
        gc.return_value.__enter__.return_value = "conn"
        rc = market_ingest.main([])
    assert rc == 0
    assert collect.call_args.args[0] == "conn"
    # provider_factory 为可调用工厂
    assert callable(collect.call_args.args[1])


def test_market_flag_ignored_no_gate(monkeypatch):
    """US/KR 上线后 --market 不再门控：任意值（含 US）正常进入增量模式。"""
    collect = MagicMock(return_value={"daily": {}})
    with patch("db.instrument.ingest.incremental.collect_incremental", collect), \
         patch("backend.workers.market_ingest.get_connection") as gc:
        gc.return_value.__enter__.return_value = "conn"
        rc = market_ingest.main(["--market", "US"])
    assert rc == 0
    assert collect.call_args.args[0] == "conn"


def test_start_after_end_rejected(capsys):
    rc = market_ingest.main(["--start", "2026-02-01", "--end", "2026-01-01"])
    assert rc == 2


def test_days_computes_start_when_no_explicit_start(monkeypatch):
    run_backfill = MagicMock(return_value={"index": {"bars": 0, "factors": 0},
                                           "days_done": 0, "failed_days": []})
    with patch("db.instrument.ingest.backfill.run_backfill", run_backfill), \
         patch("backend.workers.market_ingest.get_connection") as gc:
        gc.return_value.__enter__.return_value = "conn"
        rc = market_ingest.main(["--skip-bars", "--days", "30"])
    assert rc == 0
    start = date.fromisoformat(run_backfill.call_args.args[1])
    assert start == date.today() - timedelta(days=30)

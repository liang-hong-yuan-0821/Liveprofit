"""单元测试：个股因子历史回填（db.instrument.ingest.backfill，2026-09-22 用户拍板）。

- _snapshot_covers：按本地日线逐日反连接后的精确缺口数
- _backfill_one_stock：正常入库、上游 None 按瞬态失败重试、空帧直接放弃、列缺失跳过
- run_stock_factor_backfill：断点跳过、失败清单写入、汇总字段
- 失败清单读写：往返 + 坏文件按空清单
- CLI 互斥：--stock-factors 与指数回填参数互斥
"""

import json
import sys
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest import backfill as bf
from db.instrument.ingest.stock_factors import REQUIRED_QFQ


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _factor_frame() -> pd.DataFrame:
    """含策略 qfq 与 K 线 bfq 必需列的合法单行因子帧。"""
    cols = ["trade_date", *REQUIRED_QFQ, *bf.REQUIRED_BFQ]
    data = {c: (["2026-09-18"] if c == "trade_date" else [1.0]) for c in cols}
    return pd.DataFrame(data)


def _fake_conn(
    codes: list[tuple[str, str | None]], snapshot: dict[str, int]
) -> MagicMock:
    conn = MagicMock()
    results = iter([_Rows(codes), _Rows(list(snapshot.items()))])
    def execute(sql, params=None):
        if "pg_try_advisory_lock" in sql:
            return MagicMock(fetchone=lambda: (True, 123))
        if "pg_backend_pid" in sql:
            return MagicMock(fetchone=lambda: (123,))
        if "pg_advisory_unlock" in sql:
            return MagicMock(fetchone=lambda: (True,))
        return next(results)
    conn.execute.side_effect = execute
    return conn


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _mock_upsert(monkeypatch):
    """真 bulk_upsert 写库（需要真连接），单测替换为 mock。"""
    upsert = MagicMock()
    monkeypatch.setattr(bf, "bulk_upsert_factor_daily", upsert)
    return upsert


@pytest.fixture
def tmp_stock_failure_path(tmp_path, monkeypatch):
    p = tmp_path / "stock_factor_failures.json"
    monkeypatch.setattr(bf, "STOCK_FACTOR_FAILURE_LIST_PATH", p)
    return p


# ==================== _snapshot_covers ====================

def test_snapshot_covers_none_not_covered():
    assert bf._snapshot_covers(None) is False


def test_snapshot_covers_zero_missing_rows():
    assert bf._snapshot_covers(0) is True


def test_snapshot_covers_any_missing_rows_not_covered():
    assert bf._snapshot_covers(1) is False


def test_factor_cover_snapshot_passes_window_and_returns_missing_counts():
    conn = MagicMock()
    conn.execute.return_value = _Rows([("600000.SH", 0), ("603790.SH", 122)])

    snapshot = bf._factor_cover_snapshot(conn, "2010-01-04", "2026-09-18")

    assert snapshot == {"600000.SH": 0, "603790.SH": 122}
    assert conn.execute.call_args.args[1] == ("2010-01-04", "2010-01-04", "2026-09-18")


def test_factor_cover_snapshot_keeps_no_bar_stock_uncovered():
    conn = MagicMock()
    conn.execute.return_value = _Rows([("603790.SH", None)])

    assert bf._factor_cover_snapshot(conn, "2016-01-01", "2026-09-18") == {
        "603790.SH": None
    }


def test_stock_factor_default_window_matches_index_default():
    assert bf.STOCK_FACTOR_START_DEFAULT == bf.BACKFILL_START_DEFAULT == "2016-01-01"


# ==================== _backfill_one_stock ====================

def test_backfill_one_stock_upserts_and_commits():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = _factor_frame()

    assert bf._backfill_one_stock(conn, provider, "600000.SH", "2010-01-04", "2026-09-18") is True

    provider.get_stock_factor_df.assert_called_once_with("600000.SH", "2010-01-04", "2026-09-18")
    upsert_call = bf.bulk_upsert_factor_daily.call_args
    assert upsert_call[1]["update"] is True
    frame = upsert_call[0][1]
    assert frame["ts_code"].iloc[0] == "600000.SH"
    assert "ma_qfq_5" in frame.columns and "ma_bfq_5" in frame.columns  # bfq+qfq 同帧
    conn.commit.assert_called_once()


def test_backfill_one_stock_none_retried_then_failed():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = None  # provider 吞异常返回 None → 瞬态失败重试

    assert bf._backfill_one_stock(conn, provider, "600000.SH", "2010-01-04", "2026-09-18") is False
    assert provider.get_stock_factor_df.call_count == bf.RETRY_COUNT
    bf.bulk_upsert_factor_daily.assert_not_called()


def test_backfill_one_stock_empty_frame_gives_up_immediately():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = pd.DataFrame()  # 上游明确无数据 → 不重试

    assert bf._backfill_one_stock(conn, provider, "600000.SH", "2010-01-04", "2026-09-18") is False
    assert provider.get_stock_factor_df.call_count == 1


def test_backfill_one_stock_missing_columns_skips():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = pd.DataFrame({"trade_date": ["2026-09-18"]})

    assert bf._backfill_one_stock(conn, provider, "600000.SH", "2010-01-04", "2026-09-18") is False
    bf.bulk_upsert_factor_daily.assert_not_called()


def test_backfill_one_stock_rejects_incomplete_bfq_contract():
    conn = MagicMock()
    provider = MagicMock()
    frame = _factor_frame().drop(columns=["ma_bfq_10"])
    provider.get_stock_factor_df.return_value = frame

    assert bf._backfill_one_stock(
        conn, provider, "600000.SH", "2016-01-01", "2026-09-18"
    ) is False
    bf.bulk_upsert_factor_daily.assert_not_called()


# ==================== run_stock_factor_backfill ====================

def test_run_backfill_skips_covered_and_records_failures(tmp_stock_failure_path):
    codes = [
        ("000001.SZ", "1991-04-03"),   # 老股，快照已覆盖 → 跳过
        ("600000.SH", "1999-11-10"),
        ("603790.SH", "2018-09-12"),
        ("688981.SH", "2020-07-16"),   # 新股，快照自上市日起完整 → 跳过
    ]
    conn = _fake_conn(
        codes,
        snapshot={
            "000001.SZ": 0,
            "600000.SH": 250,
            "603790.SH": 122,
            "688981.SH": 0,
        },
    )
    provider = MagicMock()
    provider.get_stock_factor_df.side_effect = [
        _factor_frame(),   # 600000.SH 成功
        None,              # 603790.SH 失败（重试后仍 None）
    ]

    summary = bf.run_stock_factor_backfill(
        conn, "2010-01-04", "2026-09-18", sleep_seconds=0, provider_factory=lambda: provider,
    )

    assert summary["total"] == 4
    assert summary["done"] == 1
    assert summary["skipped"] == 2
    assert summary["failed"] == 1
    assert summary["failed_codes"] == ["603790.SH"]
    # 失败清单落盘：可 --retry-failed 补拉
    saved = json.loads(tmp_stock_failure_path.read_text(encoding="utf-8"))
    assert saved == ["603790.SH"]


def test_run_backfill_retry_failed_mode_ignores_snapshot(tmp_stock_failure_path):
    tmp_stock_failure_path.write_text(
        json.dumps(["603790.SH"], ensure_ascii=False), encoding="utf-8")
    conn = _fake_conn([], snapshot={})  # retry 模式不查代码清单/快照
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = _factor_frame()

    summary = bf.run_stock_factor_backfill(
        conn, "2010-01-04", "2026-09-18", sleep_seconds=0,
        retry_failed=True, provider_factory=lambda: provider,
    )

    assert summary["total"] == 1
    assert summary["done"] == 1
    assert summary["failed_codes"] == []  # 补拉成功后清单清空
    assert json.loads(tmp_stock_failure_path.read_text(encoding="utf-8")) == []


def test_run_backfill_fails_fast_when_provider_disconnected(tmp_stock_failure_path):
    conn = _fake_conn([("600000.SH", "1999-11-10")], snapshot={"600000.SH": 1})
    provider = MagicMock()
    provider.connected = False

    with pytest.raises(RuntimeError, match="数据源未连接"):
        bf.run_stock_factor_backfill(
            conn, "2010-01-04", "2026-09-18", provider_factory=lambda: provider,
        )
    provider.get_stock_factor_df.assert_not_called()


def test_run_backfill_limit_only_processes_sample(tmp_stock_failure_path):
    codes = [("000001.SZ", "1991-04-03"), ("600000.SH", "1999-11-10")]
    conn = _fake_conn(codes, snapshot={"000001.SZ": 1, "600000.SH": 1})
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = _factor_frame()

    summary = bf.run_stock_factor_backfill(
        conn, "2010-01-04", "2026-09-18", sleep_seconds=0,
        provider_factory=lambda: provider, limit=1,
    )

    assert summary["total"] == 1
    provider.get_stock_factor_df.assert_called_once()


def test_limited_run_preserves_failures_outside_sample(tmp_stock_failure_path):
    tmp_stock_failure_path.write_text(
        json.dumps(["603790.SH"], ensure_ascii=False), encoding="utf-8"
    )
    codes = [("000001.SZ", "1991-04-03"), ("603790.SH", "2018-09-12")]
    conn = _fake_conn(codes, snapshot={"000001.SZ": 1, "603790.SH": 1})
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = _factor_frame()

    summary = bf.run_stock_factor_backfill(
        conn, "2010-01-04", "2026-09-18", sleep_seconds=0,
        provider_factory=lambda: provider, limit=1,
    )

    assert summary["failed_codes"] == ["603790.SH"]
    assert json.loads(tmp_stock_failure_path.read_text(encoding="utf-8")) == ["603790.SH"]


def test_run_backfill_stops_after_consecutive_failures_and_saves_list(
    tmp_stock_failure_path, monkeypatch,
):
    monkeypatch.setattr(bf, "STOCK_FACTOR_MAX_CONSECUTIVE_FAILURES", 2)
    codes = [("000001.SZ", "1991-04-03"), ("600000.SH", "1999-11-10")]
    conn = _fake_conn(codes, snapshot={"000001.SZ": 1, "600000.SH": 1})
    provider = MagicMock()
    provider.get_stock_factor_df.return_value = pd.DataFrame()

    with pytest.raises(RuntimeError, match="连续 2 只失败"):
        bf.run_stock_factor_backfill(
            conn, "2016-01-01", "2026-09-18", sleep_seconds=0,
            provider_factory=lambda: provider,
        )

    assert json.loads(tmp_stock_failure_path.read_text(encoding="utf-8")) == [
        "000001.SZ", "600000.SH"
    ]


# ==================== 失败清单读写 ====================

def test_failed_codes_list_roundtrip_and_corrupt_file(tmp_stock_failure_path):
    assert bf._read_failed_stock_codes() == []  # 不存在 → 空
    bf._write_failed_stock_codes(["600000.SH", "000001.SZ"])
    assert bf._read_failed_stock_codes() == ["000001.SZ", "600000.SH"]  # 排序存储
    tmp_stock_failure_path.write_text("{bad json", encoding="utf-8")
    assert bf._read_failed_stock_codes() == []  # 坏文件按空清单处理


# ==================== CLI 互斥 ====================

def test_cli_stock_factors_rejects_index_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill", "--stock-factors", "--skip-concepts"])
    with pytest.raises(SystemExit):
        bf.main()


@pytest.mark.parametrize(
    "argv",
    [
        ["backfill", "--retry-failed"],
        ["backfill", "--stock-factors", "--sleep", "-1"],
        ["backfill", "--stock-factors", "--limit", "0"],
        ["backfill", "--stock-factors", "--start", "2026-09-19", "--end", "2026-09-18"],
    ],
)
def test_cli_rejects_invalid_stock_factor_arguments(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        bf.main()

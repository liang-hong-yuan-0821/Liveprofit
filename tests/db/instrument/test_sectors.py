"""板块体系双来源采集循环测试（迁移自 tests/dataflows/store/test_concepts 的
provider-mock loop 部分，CR F7——concept→sector、concept_code→sector_code）。

- 双来源逐板块拉取循环：ths 与 dc 各自循环调用 get_concept_members_df，
  dc 传最近交易日（trade_cal 末位）、ths 忽略
- 失败域分层：单板块失败仅跳过该板块、单来源失败不阻断另一来源
- dc 无最近交易日快照 → 列表入库、成分采集跳过
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest import sectors as scts


def _sector_list_df(codes, source_col=True):
    df = pd.DataFrame({
        "ts_code": codes,
        "name": [f"板块{c}" for c in codes],
        "count": [1] * len(codes),
        "exchange": ["A"] * len(codes),
        "list_date": ["20200101"] * len(codes),
        "type": ["N"] * len(codes),
    })
    if source_col:
        df["source"] = ["ths"] * len(codes)
    return df


def _members_df(ts_codes):
    return pd.DataFrame({"sector_code": ["C"] * len(ts_codes),
                         "ts_code": ts_codes})


def _mock_provider(ths_codes=("883300.TI",), dc_codes=("BK1753",),
                   members=None, list_side_effect=None):
    prov = MagicMock()
    prov.get_trade_cal.return_value = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-08-27", "2026-08-28"]),
        "is_open": 1,
    })
    if list_side_effect is not None:
        prov.get_concept_list_df.side_effect = list_side_effect
    else:
        prov.get_concept_list_df.side_effect = (
            lambda source: _sector_list_df(ths_codes) if source == "ths"
            else _sector_list_df(dc_codes))
    if members is not None:
        prov.get_concept_members_df.side_effect = (
            lambda code, source, trade_date=None: members.get((source, code)))
    else:
        prov.get_concept_members_df.return_value = _members_df(["000001.SZ"])
    return prov


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(scts.time, "sleep", lambda s: None)


@pytest.fixture
def _fake_conn(monkeypatch):
    conn = MagicMock()
    recorded = {}
    monkeypatch.setattr(scts, "upsert_sectors",
                        lambda c, df: recorded.setdefault("sectors", []).append(df) or len(df))
    monkeypatch.setattr(scts, "upsert_sector_members",
                        lambda c, df: recorded.setdefault("members", []).append(df) or len(df))
    return conn, recorded


def test_dual_source_loop_and_dc_trade_date(_fake_conn):
    conn, recorded = _fake_conn
    members = {
        ("ths", "883300.TI"): _members_df(["000001.SZ"]),
        ("ths", "883301.TI"): _members_df(["600000.SH"]),
        ("dc", "BK1753"): _members_df(["301630.SZ"]),
        ("dc", "BK1754"): _members_df(["300750.SZ"]),
    }
    prov = _mock_provider(ths_codes=("883300.TI", "883301.TI"),
                          dc_codes=("BK1753", "BK1754"), members=members)
    result = scts.collect_sectors(conn, prov)

    # ths 逐板块循环：trade_date 忽略（传 None）
    ths_calls = [c for c in prov.get_concept_members_df.call_args_list
                 if c.args[1] == "ths"]
    assert [(c.args[0], c.kwargs.get("trade_date")) for c in ths_calls] == [
        ("883300.TI", None), ("883301.TI", None)]
    # dc 传最近交易日（trade_cal 末位 2026-08-28）
    dc_calls = [c for c in prov.get_concept_members_df.call_args_list
                if c.args[1] == "dc"]
    assert [(c.args[0], c.kwargs.get("trade_date")) for c in dc_calls] == [
        ("BK1753", "20260828"), ("BK1754", "20260828")]

    assert result["ths"]["concepts"] == 2 and result["dc"]["concepts"] == 2
    assert result["ths"]["members"] == 2 and result["dc"]["members"] == 2
    assert result["ths"]["failed"] == [] and result["dc"]["failed"] == []
    assert conn.commit.call_count == 2   # 按来源独立提交

    # 两张表 source 列由采集层附加
    sector_dfs = recorded["sectors"]
    assert [df["source"].iloc[0] for df in sector_dfs] == ["ths", "dc"]
    for df in recorded["members"]:
        assert df["source"].iloc[0] in ("ths", "dc")


def test_single_sector_failure_only_skips_that_sector(_fake_conn):
    conn, recorded = _fake_conn
    members = {
        ("ths", "883300.TI"): None,                          # 失败（None）
        ("ths", "883301.TI"): pd.DataFrame(),                # 空 = 无 A 股成分，不算失败
        ("ths", "883302.TI"): _members_df(["000001.SZ"]),    # 成功
    }
    prov = _mock_provider(ths_codes=("883300.TI", "883301.TI", "883302.TI"),
                          members=members)
    result = scts.collect_sectors(conn, prov)
    assert result["ths"]["members"] == 1
    assert result["ths"]["failed"] == ["883300.TI"]


def test_single_source_failure_does_not_block_other(_fake_conn):
    conn, _ = _fake_conn

    def side(source):
        if source == "ths":
            return _sector_list_df(("883300.TI",))
        raise RuntimeError("dc 列表挂了")

    prov = _mock_provider(list_side_effect=side)
    result = scts.collect_sectors(conn, prov)
    assert result["ths"]["members"] == 1 and result["ths"]["error"] is None
    assert result["dc"]["error"] is not None
    assert result["dc"]["members"] == 0


def test_dc_without_trade_date_skips_members_but_keeps_list(_fake_conn):
    conn, recorded = _fake_conn
    prov = _mock_provider(ths_codes=("883300.TI",), dc_codes=("BK1753",))
    prov.get_trade_cal.return_value = None   # trade_cal 不可用
    result = scts.collect_sectors(conn, prov)
    assert result["dc"]["concepts"] == 1     # 列表已入库
    assert result["dc"]["members"] == 0      # 成分跳过
    assert "无最近交易日" in result["dc"]["error"]
    # ths 不受影响
    assert result["ths"]["members"] == 1


def test_members_exception_records_failed(_fake_conn):
    conn, _ = _fake_conn
    prov = _mock_provider(ths_codes=("883300.TI",))
    prov.get_concept_members_df.side_effect = RuntimeError("成分接口异常")
    result = scts.collect_sectors(conn, prov)
    assert result["ths"]["failed"] == ["883300.TI"]
    assert result["ths"]["members"] == 0


def test_source_sql_failure_rolls_back_before_next_source(_fake_conn,
                                                          monkeypatch):
    """回归：来源级 SQL 失败（COPY 语法错误等）使事务 abort，不回滚会污染
    下一来源的 DB 写入（"current transaction is aborted"）。"""
    conn, _ = _fake_conn
    prov = _mock_provider(ths_codes=("883300.TI",), dc_codes=("BK1753",))
    original = scts.upsert_sectors

    def flaky_upsert(c, df):
        if df["source"].iloc[0] == "ths":
            raise RuntimeError("SQL 级失败（事务 abort）")
        return original(c, df)

    monkeypatch.setattr(scts, "upsert_sectors", flaky_upsert)
    result = scts.collect_sectors(conn, prov)
    assert result["ths"]["error"] is not None
    assert result["dc"]["members"] == 1   # 回滚后 dc 来源正常写入
    assert conn.rollback.call_count == 1  # 仅回滚 ths 半截写入
    assert conn.commit.call_count == 1    # dc 成功独立提交


def test_prior_source_commit_survives_later_source_failure(_fake_conn,
                                                           monkeypatch):
    """回归（2026-08-30 实测踩坑）：不按来源独立提交时，后一来源失败的回滚
    会把已成功的前一来源成果一并清空。"""
    conn, _ = _fake_conn
    prov = _mock_provider(ths_codes=("883300.TI",), dc_codes=("BK1753",))

    def side(source):
        if source == "ths":
            return _sector_list_df(("883300.TI",))
        raise RuntimeError("dc 列表挂了")

    prov.get_concept_list_df.side_effect = side
    result = scts.collect_sectors(conn, prov)
    assert result["ths"]["members"] == 1   # ths 成果保留（已独立提交）
    assert result["dc"]["error"] is not None
    assert conn.commit.call_count == 2
    assert conn.rollback.call_count == 0

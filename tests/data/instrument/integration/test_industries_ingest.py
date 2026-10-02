# test-catalog-begin
# {
#   "purpose": "申万行业成员采集与验收集成测试（plan 4.3.1/4.3.3，mock provider，不触真实 token）。",
#   "keywords": [
#     "证券数据",
#     "数据完整性",
#     "数据采集",
#     "行情刷新",
#     "industries_ingest",
#     "coverage",
#     "ingest",
#     "refresh"
#   ],
#   "covers": [
#     "db/instrument/dao/ingest_state.py",
#     "db/instrument/db.py",
#     "db/instrument/ingest/industries.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""申万行业成员采集与验收集成测试（plan 4.3.1/4.3.3，mock provider，不触真实 token）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg
import pytest

from db.instrument.dao import ingest_state as state_dao
from db.instrument.ingest.industries import collect_industries

SEED_SQL = "SELECT source, industry_code, name FROM market.industry WHERE source='SW2021' ORDER BY industry_code"


class FakeProvider:
    """mock provider：classify 帧 + 每行业 con_code 映射。"""

    def __init__(self, classify_rows: list[tuple[str, str, str]], members: dict[str, list[str]]):
        self._classify = classify_rows
        self._members = members

    def get_industry_classify_df(self):
        if self._classify is None:
            return None
        return pd.DataFrame(self._classify, columns=["source", "industry_code", "name"])

    def get_industry_members_df(self, industry_index_code: str):
        code = industry_index_code.replace(".SI", "")
        con_codes = self._members.get(code)
        if con_codes is None:
            return None
        return pd.DataFrame(
            {"index_code": [industry_index_code] * len(con_codes), "con_code": con_codes}
        )


def _conn(pg_env):
    conn = psycopg.connect(pg_env["psycopg_dsn"], connect_timeout=5)
    return conn


@pytest.fixture
def seeded(pg_env, clean_market_state):
    """清空后重跑幂等 init_schema：恢复 31 行行业字典种子，返回可用 conn。"""
    conn = _conn(pg_env)
    try:
        from db.instrument.db import init_schema

        assert init_schema(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _seed_rows(conn) -> list[tuple[str, str, str]]:
    return [(r[0], r[1], r[2]) for r in conn.execute(SEED_SQL).fetchall()]


def _seed_active_stocks(conn, ts_codes: list[str]) -> None:
    with conn.cursor() as cur:
        for ts in ts_codes:
            cur.execute(
                "INSERT INTO market.instrument (ts_code, name, instrument_type, list_status, updated_at) "
                "VALUES (%s, %s, 'stock', 'L', now()) ON CONFLICT (ts_code) DO NOTHING",
                (ts, ts),
            )


def _members_table(conn) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in conn.execute(
        "SELECT industry_code, ts_code FROM market.industry_member WHERE source='SW2021'"
    ).fetchall()}


def _fake_success_members(seed: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    """每行业一个成分：600000.SH + 序号。"""
    members = {}
    for idx, (_, code, _name) in enumerate(seed):
        members[code] = [f"6{idx:05d}.SH"]
    return members


def test_full_success_atomic_refresh(seeded):
    conn = seeded
    try:
        seed = _seed_rows(conn)
        assert len(seed) == 31
        ts_codes = [f"6{i:05d}.SH" for i in range(31)]
        _seed_active_stocks(conn, ts_codes)
        conn.commit()

        provider = FakeProvider(seed, _fake_success_members(seed))
        result = collect_industries(conn, provider, request_interval=0)
        assert result["status"] == "SUCCESS"
        assert result["coverage"] == 1.0
        assert result["covered_active"] == 31
        assert result["active_total"] == 31
        assert result["active_members"] == 31

        rows = _members_table(conn)
        assert len(rows) == 31
        state = state_dao.get_state(conn, "industry_member", "SW2021")
        assert state["status"] == "SUCCESS"
        assert state["member_hash"] == state_dao.member_set_hash([(c, t) for c, t in rows])
        count = conn.execute(
            "SELECT count FROM market.industry WHERE source='SW2021' AND industry_code='801010'"
        ).fetchone()[0]
        assert count == 1
        # 门控谓词：SUCCESS + hash 一致 → 可用
        assert state_dao.is_industry_bucket_available(conn)[0] is True
    finally:
        conn.close()


def test_single_industry_failure_keeps_old_members_and_success_fields(seeded):
    conn = seeded
    try:
        seed = _seed_rows(conn)
        # 旧成功验收 + 旧成员集
        old_rows = [(seed[0][1], "600000.SH")]
        conn.execute(
            "INSERT INTO market.industry_member (source, industry_code, ts_code) VALUES ('SW2021', %s, %s)",
            (seed[0][1], "600000.SH"),
        )
        state_dao.record_success(
            conn, "industry_member", "SW2021",
            coverage=0.99, member_hash=state_dao.member_set_hash(old_rows),
        )
        conn.commit()
        old_state = state_dao.get_state(conn, "industry_member", "SW2021")

        members = _fake_success_members(seed)
        members[seed[0][1]] = None  # 第一个行业请求失败
        provider = FakeProvider(seed, members)
        result = collect_industries(conn, provider, request_interval=0)
        assert result["status"] == "FAILED"

        # 旧成员集合保持不变
        assert _members_table(conn) == {(seed[0][1], "600000.SH")}
        # 失败短事务只写失败字段，不覆盖旧成功字段（status 属成功字段，也不改写）
        new_state = state_dao.get_state(conn, "industry_member", "SW2021")
        assert new_state["failure_code"] == "INDUSTRY_COLLECT_FAILED"
        assert new_state["successful_at"] == old_state["successful_at"]
        assert new_state["member_hash"] == old_state["member_hash"]
        assert new_state["coverage"] == old_state["coverage"]
        assert new_state["status"] == "SUCCESS"
        # 门控：最近成功验收未被失败观测覆盖 → 仍可用（旧成员集合保持不变）
        assert state_dao.is_industry_bucket_available(conn)[0] is True
    finally:
        conn.close()


def test_dict_drift_rejected(seeded):
    conn = seeded
    try:
        seed = _seed_rows(conn)
        drifted = list(seed)
        drifted[0] = (drifted[0][0], drifted[0][1], "改名行业")
        provider = FakeProvider(drifted, {})
        result = collect_industries(conn, provider, request_interval=0)
        assert result["status"] == "FAILED"
        assert result["error"] == "INDUSTRY_DICT_DRIFT"
        state = state_dao.get_state(conn, "industry_member", "SW2021")
        assert state["failure_code"] == "INDUSTRY_DICT_DRIFT"
    finally:
        conn.close()


def test_coverage_gate_below_095(seeded):
    """覆盖率口径（活跃股票）：31 行业全成功但仅覆盖部分活跃票 → 拒绝（防截断帧漏放）。"""
    conn = seeded
    try:
        seed = _seed_rows(conn)
        # 100 只活跃票，行业成员只覆盖前 60 只 → coverage = 0.6 < 0.95
        _seed_active_stocks(conn, [f"6{i:05d}.SH" for i in range(100)])
        conn.commit()
        members = {}
        for idx, (_, code, _name) in enumerate(seed):
            if idx < 20:
                members[code] = [f"6{i:05d}.SH" for i in range(idx * 3, idx * 3 + 3)]  # 60 只
            else:
                members[code] = [f"6{idx + 40:05d}.SH"]  # 剩余行业各一只（避开前 60 只，避免多归属失败）
        provider = FakeProvider(seed, members)
        result = collect_industries(conn, provider, request_interval=0)
        assert result["status"] == "FAILED"
        assert result["coverage"] < 0.95
        assert result["covered_active"] < result["active_total"]
        assert _members_table(conn) == set()
        state = state_dao.get_state(conn, "industry_member", "SW2021")
        assert state["failure_code"] == "INDUSTRY_COVERAGE_LOW"
    finally:
        conn.close()


def test_multi_ownership_fails_and_non_active_filtered(seeded):
    conn = seeded
    try:
        seed = _seed_rows(conn)
        members = _fake_success_members(seed)
        code_a, code_b = seed[0][1], seed[1][1]
        # 同一活跃票出现在两个行业 → 多归属失败；再附一个非活跃票（应被过滤）
        members[code_a] = ["600000.SH", "999999.SZ"]
        members[code_b] = ["600000.SH"]
        _seed_active_stocks(conn, ["600000.SH"])
        conn.commit()
        provider = FakeProvider(seed, members)
        result = collect_industries(conn, provider, request_interval=0)
        assert result["status"] == "FAILED"
        assert any(f["reason"].startswith("MULTI_OWNERSHIP") for f in result["failed"])
        # 非活跃票不落成员表
        assert _members_table(conn) == set()
    finally:
        conn.close()


def test_bucket_gate_expiry_and_hash_mismatch(seeded):
    conn = seeded
    try:
        seed = _seed_rows(conn)
        _seed_active_stocks(conn, [f"6{i:05d}.SH" for i in range(31)])
        conn.commit()
        provider = FakeProvider(seed, _fake_success_members(seed))
        assert collect_industries(conn, provider)["status"] == "SUCCESS"
        assert state_dao.is_industry_bucket_available(conn)[0] is True

        # 成员集合被改（hash 不匹配）→ 不可用
        conn.execute(
            "DELETE FROM market.industry_member WHERE source='SW2021' AND industry_code=%s",
            (seed[0][1],),
        )
        conn.commit()
        assert state_dao.is_industry_bucket_available(conn) == (False, "INDUSTRY_BUCKET_UNAVAILABLE")

        # 恢复后过期（successful_at 拨到 10 天前）→ 不可用
        assert collect_industries(conn, provider)["status"] == "SUCCESS"
        conn.execute(
            "UPDATE market.ingest_state SET successful_at = %s "
            "WHERE resource='industry_member' AND source='SW2021'",
            (datetime.now(timezone.utc) - timedelta(days=10),),
        )
        conn.commit()
        assert state_dao.is_industry_bucket_available(conn) == (False, "INDUSTRY_BUCKET_UNAVAILABLE")
    finally:
        conn.close()

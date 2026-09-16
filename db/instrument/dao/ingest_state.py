"""market.ingest_state 验收状态 DAO（plan 4.3.1）。

单行混存最近成功验收与最近失败观测：成功事务只写成功字段
（status/successful_at/coverage/member_hash），失败短事务只写失败字段
（status/failure_code/summary_json），互不覆盖。
另含行业 BUY 门控谓词 is_industry_bucket_available（T7 PositionPlanner 消费）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

TABLE = "market.ingest_state"
RESOURCE_INDUSTRY_MEMBER = "industry_member"
SOURCE_SW2021 = "SW2021"
MIN_COVERAGE = 0.95
MAX_AGE_DAYS = 8


def member_set_hash(rows: list[tuple[str, str]]) -> str:
    """成员集合 hash：排序后的 industry_code:ts_code 行的 SHA-256（成功侧与门控侧共用）。"""
    payload = "\n".join(sorted(f"{code}:{ts}" for code, ts in rows))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_success(
    conn,
    resource: str,
    source: str,
    *,
    coverage: float,
    member_hash: str | None,
    summary: dict | None = None,
    observed_at: datetime | None = None,
) -> None:
    """成功验收：只写成功字段，失败字段清空；summary 用于 POC 等需要留验收证据的场景。"""
    observed_at = observed_at or datetime.now(timezone.utc)
    conn.execute(
        f"""
        INSERT INTO {TABLE} (resource, source, status, successful_at, coverage, member_hash, failure_code, summary_json, observed_at)
        VALUES (%s, %s, 'SUCCESS', %s, %s, %s, NULL, %s::jsonb, %s)
        ON CONFLICT (resource, source) DO UPDATE SET
            status = EXCLUDED.status,
            successful_at = EXCLUDED.successful_at,
            coverage = EXCLUDED.coverage,
            member_hash = EXCLUDED.member_hash,
            failure_code = NULL,
            summary_json = EXCLUDED.summary_json,
            observed_at = EXCLUDED.observed_at
        """,
        (
            resource,
            source,
            observed_at,
            coverage,
            member_hash,
            json.dumps(summary, ensure_ascii=False) if summary is not None else None,
            observed_at,
        ),
    )


def record_failure(
    conn,
    resource: str,
    source: str,
    *,
    failure_code: str,
    summary: dict,
    observed_at: datetime | None = None,
) -> None:
    """失败观测：只写失败字段（failure_code/summary_json/observed_at），
    不得覆盖旧 successful_at/member_hash/coverage，也不改写 status——
    status 属成功字段，失败观测后门控仍按最近成功验收判定（旧成功成员集保持不变）。"""
    observed_at = observed_at or datetime.now(timezone.utc)
    conn.execute(
        f"""
        INSERT INTO {TABLE} (resource, source, failure_code, summary_json, observed_at)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (resource, source) DO UPDATE SET
            failure_code = EXCLUDED.failure_code,
            summary_json = EXCLUDED.summary_json,
            observed_at = EXCLUDED.observed_at
        """,
        (resource, source, failure_code, json.dumps(summary, ensure_ascii=False), observed_at),
    )


def get_state(conn, resource: str, source: str) -> dict | None:
    row = conn.execute(
        f"SELECT status, successful_at, coverage, member_hash, failure_code, summary_json, observed_at "
        f"FROM {TABLE} WHERE resource = %s AND source = %s",
        (resource, source),
    ).fetchone()
    if row is None:
        return None
    keys = ("status", "successful_at", "coverage", "member_hash", "failure_code", "summary_json", "observed_at")
    return dict(zip(keys, row))


def is_industry_bucket_available(conn, *, max_age_days: int = MAX_AGE_DAYS) -> tuple[bool, str | None]:
    """行业 BUY 门控谓词（plan 4.3.1）：
    status='SUCCESS' AND successful_at 距今 ≤ max_age_days AND coverage ≥ 0.95
    AND member_hash 与当前 industry_member SW2021 集合 hash 相等。
    冷启动、上次成功已过期、无成员或最近集合不匹配都为 INDUSTRY_BUCKET_UNAVAILABLE。
    """
    state = get_state(conn, RESOURCE_INDUSTRY_MEMBER, SOURCE_SW2021)
    if state is None or state.get("status") != "SUCCESS":
        return False, "INDUSTRY_BUCKET_UNAVAILABLE"
    successful_at = state.get("successful_at")
    if successful_at is None:
        return False, "INDUSTRY_BUCKET_UNAVAILABLE"
    if successful_at.tzinfo is None:
        successful_at = successful_at.replace(tzinfo=timezone.utc)
    if successful_at < datetime.now(timezone.utc) - timedelta(days=max_age_days):
        return False, "INDUSTRY_BUCKET_UNAVAILABLE"
    if (state.get("coverage") or 0) < MIN_COVERAGE:
        return False, "INDUSTRY_BUCKET_UNAVAILABLE"
    rows = conn.execute(
        "SELECT industry_code, ts_code FROM market.industry_member WHERE source = %s",
        (SOURCE_SW2021,),
    ).fetchall()
    if not rows:
        return False, "INDUSTRY_BUCKET_UNAVAILABLE"
    if member_set_hash([(r[0], r[1]) for r in rows]) != state.get("member_hash"):
        return False, "INDUSTRY_BUCKET_UNAVAILABLE"
    return True, None

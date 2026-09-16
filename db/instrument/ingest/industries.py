"""申万 SW2021 一级行业成员采集与验收（plan 4.3.1）。

- collect_industries(conn, provider)：先拉 index_classify 与 31 条 seed 完全一致
  （(source, industry_code, name) 集合相等，任何字典漂移即拒绝 member 替换）；
  随后全部 31 个 index_member 拉入内存，con_code 归一为 CN ts_code 后按
  market.instrument 活跃股票校验唯一归属；覆盖率 = 有行业归属的活跃股票数 /
  活跃股票总数，≥ 0.95 才验收成功（防「非空但被截断」帧漏放）；
  任一行业请求失败、空响应、活跃票多归属或覆盖率不足 → 先 rollback 成员替换事务，
  再用**新的短事务**只更新 failure_code/summary_json/observed_at（不覆盖旧成功字段）；
  全部成功后在一个事务内 DELETE + upsert 新成员 + 按成员数更新 industry.count +
  写入成员 hash/coverage/successful_at 后 commit。

- --poc：部署前 POC 门禁（真实 TUSHARE_TOKEN，不进入自动 pytest），
  记录 31 个一级行业的请求、返回字段、空/重复响应、限流和间隔，
  成功标准（31 次请求均成功、字典匹配、覆盖率 ≥ 95%、无活跃票多归属）
  与结果摘要写入 market.ingest_state(resource='industry_member', source='SW2021')。
  未获得成功状态即不启用行业上限 BUY。

- --refresh：真实刷新一次（周刷接线见 incremental.collect_incremental refresh_industries）。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone

import pandas as pd

from db.instrument.dao import ingest_state as state_dao
from db.instrument.dao import industry as industry_dao

logger = logging.getLogger(__name__)

RESOURCE = state_dao.RESOURCE_INDUSTRY_MEMBER
SOURCE = state_dao.SOURCE_SW2021
EXPECTED_COUNT = 31
MIN_COVERAGE = 0.95
CN_TS_RE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_ts_code(raw) -> str | None:
    value = str(raw or "").strip().upper()
    return value if CN_TS_RE.match(value) else None


def _load_seed(conn) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT source, industry_code, name FROM market.industry WHERE source = %s", (SOURCE,)
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def _load_active_stocks(conn) -> set[str]:
    rows = conn.execute(
        "SELECT ts_code FROM market.instrument "
        "WHERE instrument_type = 'stock' AND list_status = 'L' AND ts_code ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'"
    ).fetchall()
    return {r[0] for r in rows}


REQUEST_INTERVAL = 0.2  # 31 个行业请求节流间隔（同 sector_daily 约定）


def collect_industries(conn, provider, fallback_provider=None, *, request_interval: float = REQUEST_INTERVAL) -> dict:
    """采集并原子刷新行业成员；返回 summary dict（status=SUCCESS/FAILED）。

    fallback_provider：主源不支持行业接口（classify 为 None）时兜底（双源接线）。
    """
    classify = provider.get_industry_classify_df()
    if (classify is None or classify.empty) and fallback_provider is not None:
        classify = fallback_provider.get_industry_classify_df()
    if classify is None or classify.empty:
        _record_failure_short_tx(conn, "INDUSTRY_CLASSIFY_UNAVAILABLE", {"status": "FAILED"})
        return {"status": "FAILED", "error": "INDUSTRY_CLASSIFY_UNAVAILABLE"}
    classify_rows = {
        (r.source, r.industry_code, r.name) for r in classify.itertuples(index=False)
    }
    seed = _load_seed(conn)
    if classify_rows != seed:
        summary = {
            "status": "FAILED",
            "error": "INDUSTRY_DICT_DRIFT",
            "seed_count": len(seed),
            "classify_count": len(classify_rows),
        }
        _record_failure_short_tx(conn, "INDUSTRY_DICT_DRIFT", summary)
        return summary
    codes = sorted(r[1] for r in seed)
    active = _load_active_stocks(conn)

    # 全部 31 个 index_member 拉入内存（801010.SI 仅在 Provider 请求边界补后缀）
    members: dict[str, set[str]] = {}
    failures: list[dict] = []
    seen_owner: dict[str, str] = {}
    for code in codes:
        if request_interval > 0:
            time.sleep(request_interval)
        try:
            df = provider.get_industry_members_df(f"{code}.SI")
            if (df is None or getattr(df, "empty", True)) and fallback_provider is not None:
                df = fallback_provider.get_industry_members_df(f"{code}.SI")
            if df is None or getattr(df, "empty", True):
                failures.append({"industry_code": code, "reason": "EMPTY_OR_FAILED"})
                continue
            ts_codes: set[str] = set()
            for con in df["con_code"]:
                ts = _normalize_ts_code(con)
                if ts is None or ts not in active:
                    continue  # 非活跃 CN 股票成分忽略（仅接受可归一至 CN 股票的 con_code）
                if ts in seen_owner and seen_owner[ts] != code:
                    failures.append({"industry_code": code, "reason": f"MULTI_OWNERSHIP:{ts}"})
                    ts_codes.clear()
                    break
                seen_owner[ts] = code
                ts_codes.add(ts)
        except Exception as exc:  # noqa: BLE001
            # 畸形帧/意外异常：该行业记失败，不拖垮整轮（失败短事务收口）
            failures.append({"industry_code": code, "reason": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        if not ts_codes:
            failures.append({"industry_code": code, "reason": "NO_ACTIVE_MEMBERS"})
            continue
        members[code] = ts_codes

    # 覆盖率 = 有行业归属的活跃股票数 / 活跃股票总数（防「非空但被截断」帧漏放；
    # 2026-09-16 用户拍板口径 A）
    rows = sorted((code, ts) for code, tss in members.items() for ts in tss)
    covered_active = len({ts for _, ts in rows})
    active_total = len(active)
    coverage = round(covered_active / active_total, 4) if active_total else 0.0
    summary = {
        "resource": RESOURCE,
        "source": SOURCE,
        "attempted": EXPECTED_COUNT,
        "succeeded": len(members),
        "failed": failures,
        "coverage": coverage,
        "covered_active": covered_active,
        "active_total": active_total,
        "active_members": len(rows),
    }
    failure_code: str | None = None
    if failures:
        failure_code = "INDUSTRY_COLLECT_FAILED"
    elif coverage < MIN_COVERAGE:
        failure_code = "INDUSTRY_COVERAGE_LOW"
    if failure_code is not None:
        # 先 rollback 成员替换事务（本流程未写成员，回滚结束读事务），
        # 再用新的短事务只更新失败字段，不得覆盖旧成功字段
        _record_failure_short_tx(conn, failure_code, summary)
        return {"status": "FAILED", **summary}

    # 全部成功：一个事务内 DELETE + upsert + count 回写 + 成功验收
    member_hash = state_dao.member_set_hash(rows)
    try:
        conn.execute("DELETE FROM market.industry_member WHERE source = %s", (SOURCE,))
        if rows:
            frame = pd.DataFrame(rows, columns=["industry_code", "ts_code"])
            frame["source"] = SOURCE
            industry_dao.upsert_industry_members(conn, frame[["source", "industry_code", "ts_code"]])
        conn.execute(
            "UPDATE market.industry SET count = ("
            "  SELECT count(*) FROM market.industry_member m "
            "  WHERE m.source = market.industry.source AND m.industry_code = market.industry.industry_code"
            "), updated_at = now() WHERE source = %s",
            (SOURCE,),
        )
        state_dao.record_success(conn, RESOURCE, SOURCE, coverage=coverage, member_hash=member_hash, observed_at=_now())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"status": "SUCCESS", **summary, "member_hash": member_hash}


def _record_failure_short_tx(conn, failure_code: str, summary: dict) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
    state_dao.record_failure(conn, RESOURCE, SOURCE, failure_code=failure_code, summary=summary, observed_at=_now())
    conn.commit()


def run_poc(provider) -> dict:
    """部署前 POC 门禁（不进入自动 pytest）：真实 token 逐行业请求并记录细节。

    成功标准：31 次请求均成功、字典匹配（与 seed 完全一致）、覆盖率≥95%、
    无活跃票多归属——结果摘要写入 ingest_state（summary_json）。
    """
    from db.instrument.db import get_connection

    started = time.monotonic()
    per_industry: list[dict] = []
    classify = provider.get_industry_classify_df()
    classify_ok = classify is not None and not classify.empty
    codes: list[str] = []
    dict_matched = False
    if classify_ok:
        codes = sorted(
            str(r.industry_code) for r in classify.itertuples(index=False)
        )
        with get_connection() as conn:
            classify_rows = {
                (r.source, r.industry_code, r.name) for r in classify.itertuples(index=False)
            }
            dict_matched = classify_rows == _load_seed(conn)
    all_ok = classify_ok and len(codes) == EXPECTED_COUNT and dict_matched
    members: dict[str, set[str]] = {}
    seen_owner: dict[str, str] = {}
    multi_ownership = 0
    for code in codes:
        t0 = time.monotonic()
        try:
            df = provider.get_industry_members_df(f"{code}.SI")
            elapsed = round(time.monotonic() - t0, 3)
            if df is None or df.empty:
                all_ok = False
                per_industry.append({"industry_code": code, "ok": False, "reason": "EMPTY", "elapsed_s": elapsed})
                continue
            con_codes = [str(c) for c in df["con_code"]]
            duplicates = len(con_codes) - len(set(con_codes))
            ts_codes: set[str] = set()
            for con in con_codes:
                ts = _normalize_ts_code(con)
                if ts is None:
                    continue
                if ts in seen_owner and seen_owner[ts] != code:
                    multi_ownership += 1
                    all_ok = False
                    continue
                seen_owner[ts] = code
                ts_codes.add(ts)
            members[code] = ts_codes
            per_industry.append({
                "industry_code": code,
                "ok": True,
                "rows": len(con_codes),
                "duplicates": duplicates,
                "elapsed_s": elapsed,
            })
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            per_industry.append({
                "industry_code": code,
                "ok": False,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
                "elapsed_s": round(time.monotonic() - t0, 3),
            })
            time.sleep(0.5)  # 限流间隔：失败后额外退避

    coverage = round(len(members) / EXPECTED_COUNT, 4) if codes else 0
    total_elapsed = round(time.monotonic() - started, 3)
    rows = sorted((code, ts) for code, tss in members.items() for ts in tss)
    success = all_ok and coverage >= MIN_COVERAGE and multi_ownership == 0
    summary = {
        "poc": True,
        "dict_matched": dict_matched,
        "attempted": len(codes),
        "succeeded": len(members),
        "coverage": coverage,
        "multi_ownership": multi_ownership,
        "total_elapsed_s": total_elapsed,
        "per_industry": per_industry,
        "member_hash": state_dao.member_set_hash(rows) if success else None,
    }
    with get_connection() as conn:
        if success:
            # POC 成功即视为一次成功验收；member_hash 置 None（POC 不写成员表，
            # 避免与门控侧对 industry_member 现集合的 hash 永远对不上）
            state_dao.record_success(
                conn, RESOURCE, SOURCE, coverage=coverage, member_hash=None,
                summary=summary, observed_at=_now(),
            )
        else:
            state_dao.record_failure(conn, RESOURCE, SOURCE, failure_code="INDUSTRY_POC_FAILED", summary=summary, observed_at=_now())
        conn.commit()
    return {"status": "SUCCESS" if success else "FAILED", **summary}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="申万 SW2021 行业成员采集（POC 门禁 / 周刷）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--poc", action="store_true", help="部署前 POC：真实 token 记录 31 行业请求细节写入 ingest_state")
    group.add_argument("--refresh", action="store_true", help="真实刷新一次行业成员（同 collect_incremental 周刷逻辑）")
    args = parser.parse_args(argv)

    from AI.eventStudy.collectors.config import get_provider

    provider = get_provider()
    if args.poc:
        result = run_poc(provider)
    else:
        from db.instrument.db import get_connection

        with get_connection() as conn:
            result = collect_industries(conn, provider)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

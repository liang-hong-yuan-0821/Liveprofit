"""市场数据采集入口 — thin wrapper（证券市场数据库统一方案 3.4.1）

五 flag 全集保留（--start/--end/--days/--market/--skip-bars），内部调
db.instrument.ingest：
- 指定 --start（或 --days 回溯）→ 回填模式（backfill：指数全历史内置分项 +
  个股基金逐日回填）；--skip-bars 映射内部 skip_daily=True（仅指数日线+因子）
- 未指定 → 增量模式（incremental：指数日线+因子 + 个股基金增量 + 板块周刷）
- --market 保留兼容参数、不参与门控（采集范围由 INDEX_TARGETS 决定，
  2026-09-14 US/KR 上线后含 CN 11 + US 3 + KS11）
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from db.instrument.db import get_connection


def main(argv: list[str] | None = None, *, changed=None) -> int:
    parser = argparse.ArgumentParser(
        prog="market_ingest",
        description="采集 market schema（指数日线+因子、个股基金日线+复权、板块周刷，幂等可重复执行）",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="开始日期 YYYY-MM-DD（默认 90 天前）")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=90, help="未指定 --start 时的回溯天数")
    parser.add_argument("--market", default="CN", help="保留兼容参数（采集范围由 INDEX_TARGETS 决定，不再校验）")
    parser.add_argument("--initialize-catalog", action="store_true",
                        help="仅初始化股票基础目录和东财板块字典/成员，不采行情、基金或qfq")
    parser.add_argument("--stock-directory-only", action="store_true",
                        help="与 --initialize-catalog 合用：仅更新股票目录，跳过已有板块成员刷新")
    parser.add_argument(
        "--skip-bars",
        action="store_true",
        help="跳过个股基金日线采集，仅采指数日线+技术因子（因子全历史回填用）",
    )
    parser.add_argument(
        "--refresh-industries",
        action="store_true",
        default=None,
        help="强制本次增量执行申万行业成员刷新（默认周一周刷）",
    )
    parser.add_argument(
        "--no-refresh-industries",
        action="store_false",
        dest="refresh_industries",
        help="强制跳过本次行业成员刷新",
    )
    args = parser.parse_args(argv)
    if args.stock_directory_only and not args.initialize_catalog:
        parser.error("--stock-directory-only requires --initialize-catalog")
    if args.initialize_catalog and (args.start is not None or args.skip_bars):
        parser.error("--initialize-catalog cannot be combined with --start or --skip-bars")
    if changed is None:
        from backend.modules.market_data.application.refresh_service import best_effort_market_changed
        changed = best_effort_market_changed
    from db.instrument.ingest.guard import IngestGuard
    from backend.bootstrap.settings import CoreSettings
    market_dsn = CoreSettings().resolved_market_dsn()

    if args.initialize_catalog:
        from db.instrument.ingest.refresh import initialize_catalog
        with get_connection(market_dsn) as conn, IngestGuard(conn, changed=changed) as guard:
            summary = initialize_catalog(conn, guard=guard, refresh_sectors=not args.stock_directory_only)
        print(f"目录初始化完成：{summary}")
        result = summary["sectors"].get("dc", {})
        return 1 if result.get("error") or result.get("failed") else 0

    end = args.end or date.today()
    start = args.start or (end - timedelta(days=args.days))
    if start > end:
        print(f"开始日期 {start} 晚于结束日期 {end}", file=sys.stderr)
        return 2

    # 公共装配：主/兜底源工厂对（按 LIVEPROFIT_DATA_SOURCE，CR BLOCKER 2 接线）
    from db.instrument.ingest.incremental import _providers_from_env

    provider_factory, fallback_provider_factory = _providers_from_env()

    mode = "仅指数日线+因子" if args.skip_bars else "回填"
    print(f"采集范围：{start} ~ {end}（{mode}）")

    if args.start is not None or args.skip_bars:
        # 回填模式（显式 --start 或 --skip-bars 均走 backfill 路径）
        from db.instrument.ingest.backfill import run_backfill
        with get_connection(market_dsn) as conn, IngestGuard(conn, changed=changed) as guard:
            summary = run_backfill(
                conn, start.isoformat(), end.isoformat(),
                provider_factory=provider_factory,
                fallback_provider_factory=fallback_provider_factory,
                skip_daily=args.skip_bars,
                guard=guard,
            )
        print(f"回填完成：指数 bars={summary['index'].get('bars', 0)} "
              f"factors={summary['index'].get('factors', 0)} / "
              f"个股基金 {summary['days_done']} 日 / 失败 {len(summary['failed_days'])} 日")
        return 0 if not summary["failed_days"] else 1

    # 增量模式（默认窗口）
    from db.instrument.ingest.incremental import collect_incremental
    with get_connection(market_dsn) as conn, IngestGuard(conn, changed=changed) as guard:
        summary = collect_incremental(
            conn, provider_factory, fallback_provider_factory,
            refresh_industries=args.refresh_industries, guard=guard)
    print(f"增量完成：{summary}")
    return 0 if "error" not in summary else 1


if __name__ == "__main__":
    raise SystemExit(main())

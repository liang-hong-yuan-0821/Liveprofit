"""市场数据采集入口 — thin wrapper（证券市场数据库统一方案 3.4.1）

五 flag 全集保留（--start/--end/--days/--market/--skip-bars），内部调
db.instrument.ingest：
- 指定 --start（或 --days 回溯）→ 回填模式（backfill：指数全历史内置分项 +
  个股基金逐日回填）；--skip-bars 映射内部 skip_daily=True（仅指数日线+因子）
- 未指定 → 增量模式（incremental：指数日线+因子 + 个股基金增量 + 板块周刷）
- --market 保留兼容参数、不参与门控（采集范围由 INDEX_TARGETS 决定，
  2026-09-14 US/KR 上线后含 CN 9 + US 3 + KS11）
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from db.instrument.db import get_connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="market_ingest",
        description="采集 market schema（指数日线+因子、个股基金日线+复权、板块周刷，幂等可重复执行）",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="开始日期 YYYY-MM-DD（默认 90 天前）")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=90, help="未指定 --start 时的回溯天数")
    parser.add_argument("--market", default="CN", help="保留兼容参数（采集范围由 INDEX_TARGETS 决定，不再校验）")
    parser.add_argument(
        "--skip-bars",
        action="store_true",
        help="跳过个股基金日线采集，仅采指数日线+技术因子（因子全历史回填用）",
    )
    args = parser.parse_args(argv)

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
        with get_connection() as conn:
            summary = run_backfill(
                conn, start.isoformat(), end.isoformat(),
                provider_factory=provider_factory,
                fallback_provider_factory=fallback_provider_factory,
                skip_daily=args.skip_bars,
            )
        print(f"回填完成：指数 bars={summary['index'].get('bars', 0)} "
              f"factors={summary['index'].get('factors', 0)} / "
              f"个股基金 {summary['days_done']} 日 / 失败 {len(summary['failed_days'])} 日")
        return 0 if not summary["failed_days"] else 1

    # 增量模式（默认窗口）
    from db.instrument.ingest.incremental import collect_incremental
    with get_connection() as conn:
        summary = collect_incremental(
            conn, provider_factory, fallback_provider_factory)
    print(f"增量完成：{summary}")
    return 0 if "error" not in summary else 1


if __name__ == "__main__":
    raise SystemExit(main())

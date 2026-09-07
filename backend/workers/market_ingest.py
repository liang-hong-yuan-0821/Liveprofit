"""市场读模型采集入口（后端方案 §3.1.5；python -m backend.workers.market_ingest）

- 仅采集目录中 AVAILABLE 的 CN 资产日线（US/KR 为 live 实测验收路径，验收前保持 UNAVAILABLE）；
- DataFrame → 显式 mapper → market_bars_daily 规范化表（ON CONFLICT DO UPDATE 幂等，可重复执行）；
- 每个资产独立提交：单个失败不丢其余资产成果；
- 热点快照（heat_v1）仍为 TODO 占位（见 ingestion.py docstring），本入口不落快照，
  服务端返回 NO_HOT_CONCEPTS，不伪造热度。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from backend.bootstrap.database import build_sync_engine, build_sync_session_factory
from backend.bootstrap.observability import init_logging
from backend.bootstrap.settings import Settings, SettingsValidationError
from backend.modules.market_data.infrastructure.ingestion import IndexBarsIngestion
from backend.modules.market_data.infrastructure.repositories import MarketAssetRepository, SqlAlchemyMarketUow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="market_ingest",
        description="采集市场读模型（CN 指数日线写入 market_bars_daily，幂等可重复执行）",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="开始日期 YYYY-MM-DD（默认 90 天前）")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=90, help="未指定 --start 时的回溯天数")
    parser.add_argument("--market", default="CN", help="仅支持 CN（US/KR 为 live 实测验收路径）")
    args = parser.parse_args(argv)

    if args.market != "CN":
        print("仅支持 CN 指数日线采集（US/KR 为 live 实测验收路径，验收前保持 UNAVAILABLE）", file=sys.stderr)
        return 2

    try:
        settings = Settings()
        settings.validate_for_process("market_ingestion")
    except SettingsValidationError as exc:
        print(f"market_ingest 配置校验失败：{exc}", file=sys.stderr)
        return 2

    init_logging(settings.core.log_level)

    end = args.end or date.today()
    start = args.start or (end - timedelta(days=args.days))
    if start > end:
        print(f"开始日期 {start} 晚于结束日期 {end}", file=sys.stderr)
        return 2

    engine = build_sync_engine(settings.core.resolved_database_url())
    session_factory = build_sync_session_factory(engine)

    # Provider 工厂：CN 走 Tushare 结构化接口（get_index_data_df）
    from AI.dataflows.providers.cn.tushare import TushareProvider

    def provider_factory(_market: str):
        return TushareProvider()

    uow = SqlAlchemyMarketUow(session_factory)
    with uow:
        ingestion = IndexBarsIngestion(uow, provider_factory)
        repo = MarketAssetRepository(uow.session)
        targets = [
            (asset.market, asset.symbol)
            for asset in repo.list_enabled()
            if asset.market == "CN" and asset.availability_status == "AVAILABLE"
        ]

        if not targets:
            print("目录中没有可采集的 CN AVAILABLE 资产")
            return 0

        print(f"采集范围：{start} ~ {end}，共 {len(targets)} 个 CN 资产")
        ok = 0
        failed: list[str] = []
        for market, symbol in targets:
            try:
                rows = ingestion.ingest_asset(market, symbol, start.isoformat(), end.isoformat())
                print(f"[OK] {market}:{symbol} -> {rows} 行")
                ok += 1
            except Exception as exc:  # noqa: BLE001 —— 单资产失败不阻断其余资产
                print(f"[FAIL] {market}:{symbol} -> {exc}", file=sys.stderr)
                failed.append(f"{market}:{symbol}")

    engine.dispose()
    print(f"采集完成：成功 {ok}，失败 {len(failed)}{'：' + ', '.join(failed) if failed else ''}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

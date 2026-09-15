"""存量迁移：三处旧表 → market schema（证券市场数据库统一方案 3.2）。

用法：
  python -m db.instrument.migration.migrate_legacy [--drop]

设计要点（方案 3.2.1 定稿）：
- 按序执行 8 步骤、每步每批独立 commit、断点续跑（checkpoint 进度文件
  logs/migrate_legacy_checkpoint.json——每批完成即落盘；重跑按 checkpoint 跳过
  已完成批次；checkpoint 缺失/落后时按幂等写入全量重放兜底）
- 不用行数比对做断点：instrument_daily/factor_daily 是多源合并表，整表行数
  永不等于单一步骤源行数，"一致则跳过"永不触发
- schema 限定约定（同名表风险）：迁移连接 SET search_path = public；
  源表引用与 DROP 一律 `public.` 前缀、market 目标表一律 `market.` 前缀
  （market.adj_factor 与 public.adj_factor 同名——裸表名会自引用新表）
- 所有步骤批次写入 ON CONFLICT DO NOTHING；仅步骤 2 第二源 market_bars_daily
  为 DO UPDATE 覆盖例外（该表带 source_updated_at 日终修正语义，作为同键冲突
  时的权威方——只覆盖 OHLC/vol/source/updated_at，不覆盖 amount/pre_close/
  change/pct_chg，避免抹掉 eventStudy 迁入行）
- --drop：幂等 DROP public 旧表（store 六表 + market_data + industry_codes；
  backend 三表走 alembic 0007 不在此列）
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from db.instrument.db import get_connection

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = Path("logs/migrate_legacy_checkpoint.json")

TS_CODE_BATCH = 5000           # 步骤 1/3 按 ts_code 分批大小（1400 万行单事务不可行）
MEMBER_BATCH = 500             # 步骤 5 成分按板块代码分批大小

# --drop 清单（public schema）：store 六表 + eventStudy 两表
DROP_TABLES = [
    "stock_daily", "adj_factor", "stock_basic", "fund_basic",
    "concept", "concept_member", "market_data", "industry_codes",
]

# instrument_daily 目标列（source/updated_at 由迁移填充）
DAILY_COLS = ("ts_code, trade_date, open, high, low, close, pre_close, "
              "change, pct_chg, vol, amount, source, updated_at")


class MigrationError(Exception):
    """迁移步骤失败（调用方按步骤报告，不中断后续步骤）。"""


# ==================== checkpoint ====================

def _read_checkpoint() -> dict:
    if not CHECKPOINT_PATH.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("checkpoint 读取失败（按空清单处理，全量重放兜底）: %s", e)
        return {}


def _mark_done(ckpt: dict, key: str) -> None:
    """批次完成后落盘（读-改-写；崩溃窗口内重放同批由 ON CONFLICT 兜底）。"""
    ckpt[key] = True
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_PATH.with_name(
        f"{CHECKPOINT_PATH.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(ckpt, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(CHECKPOINT_PATH)


def _table_exists(conn, qualified: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL", (qualified,)).fetchone()
    return bool(row[0]) if row else False


# ==================== 各步骤 ====================

def _step1_stock_daily(conn, ckpt: dict, migrated_at) -> dict:
    """个股基金日线直迁（source=tushare、updated_at=迁移时刻；按 ts_code 分批）。"""
    if not _table_exists(conn, "public.stock_daily"):
        return {"skipped": True, "src": 0, "dst": None}
    src = conn.execute("SELECT count(*) FROM public.stock_daily").fetchone()[0]
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT ts_code FROM public.stock_daily ORDER BY ts_code").fetchall()]
    done = 0
    for batch in _iter_batches(codes, TS_CODE_BATCH):
        key = f"1:{batch[0]}"
        if ckpt.get(key):
            done += len(batch)
            continue
        conn.execute(
            f"INSERT INTO market.instrument_daily ({DAILY_COLS}) "
            "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
            "change, pct_chg, vol, amount, 'tushare', %(at)s "
            "FROM public.stock_daily WHERE ts_code = ANY(%(codes)s) "
            "ON CONFLICT DO NOTHING",
            {"at": migrated_at, "codes": batch})
        conn.commit()
        _mark_done(ckpt, key)
        done += len(batch)
    dst = conn.execute(
        "SELECT count(*) FROM market.instrument_daily "
        "WHERE source = 'tushare' AND updated_at = %s", (migrated_at,)).fetchone()[0]
    return {"skipped": False, "src": src, "dst": dst, "batches": len(codes) // TS_CODE_BATCH + 1}


def _step2_index_daily(conn, ckpt: dict, migrated_at) -> dict:
    """指数日线合并去重：eventStudy.market_data 先迁（深 ~2 年）、
    market_bars_daily 后迁 DO UPDATE 覆盖（新 ~90 天，日终修正权威）。"""
    report = {}
    key = "2:eventstudy"
    if not ckpt.get(key):
        if _table_exists(conn, "public.market_data") and _table_exists(conn, "public.assets"):
            src = conn.execute("SELECT count(*) FROM public.market_data").fetchone()[0]
            conn.execute(
                f"INSERT INTO market.instrument_daily ({DAILY_COLS}) "
                "SELECT a.ticker, m.ts::date, m.open, m.high, m.low, m.adj_close, "
                "NULL, NULL, NULL, m.vol, m.amount, 'tushare', %(at)s "
                "FROM public.market_data m "
                "JOIN public.assets a ON a.asset_id = m.asset_id "
                "ON CONFLICT DO NOTHING",
                {"at": migrated_at})
            conn.commit()
            _mark_done(ckpt, key)
            report["eventstudy_src"] = src
        else:
            report["eventstudy_src"] = "skipped(表缺失)"
    key = "2:market_bars"
    if not ckpt.get(key):
        if _table_exists(conn, "public.market_bars_daily") and _table_exists(conn, "public.market_assets"):
            src = conn.execute("SELECT count(*) FROM public.market_bars_daily").fetchone()[0]
            conn.execute(
                f"INSERT INTO market.instrument_daily ({DAILY_COLS}) "
                "SELECT ma.symbol, b.trading_date, b.open, b.high, b.low, b.close, "
                "NULL, NULL, NULL, b.volume, NULL, b.source, b.source_updated_at "
                "FROM public.market_bars_daily b "
                "JOIN public.market_assets ma ON ma.id = b.asset_id "
                "ON CONFLICT (ts_code, trade_date) DO UPDATE SET "
                "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
                "close = EXCLUDED.close, vol = EXCLUDED.vol, "
                "source = EXCLUDED.source, updated_at = EXCLUDED.updated_at")
            conn.commit()
            _mark_done(ckpt, key)
            report["bars_src"] = src
        else:
            report["bars_src"] = "skipped(表缺失)"
    return report


def _step3_adj_factor(conn, ckpt: dict) -> dict:
    """复权因子直迁（同名表：源 public.adj_factor、目标 market.adj_factor）。"""
    if not _table_exists(conn, "public.adj_factor"):
        return {"skipped": True, "src": 0, "dst": None}
    src = conn.execute("SELECT count(*) FROM public.adj_factor").fetchone()[0]
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT ts_code FROM public.adj_factor ORDER BY ts_code").fetchall()]
    for batch in _iter_batches(codes, TS_CODE_BATCH):
        key = f"3:{batch[0]}"
        if ckpt.get(key):
            continue
        conn.execute(
            "INSERT INTO market.adj_factor (ts_code, trade_date, adj_factor) "
            "SELECT ts_code, trade_date, adj_factor FROM public.adj_factor "
            "WHERE ts_code = ANY(%s) ON CONFLICT DO NOTHING",
            (batch,))
        conn.commit()
        _mark_done(ckpt, key)
    dst = conn.execute("SELECT count(*) FROM market.adj_factor").fetchone()[0]
    return {"skipped": False, "src": src, "dst": dst}


def _step4_factors(conn, ckpt: dict) -> dict:
    """指数技术因子直迁（10 列；ma_bfq_250/rsi_* 留 NULL；close 反查填充）。"""
    report = {}
    key = "4:factors"
    if not ckpt.get(key):
        if _table_exists(conn, "public.market_index_factors") and _table_exists(conn, "public.market_assets"):
            src = conn.execute("SELECT count(*) FROM public.market_index_factors").fetchone()[0]
            conn.execute(
                "INSERT INTO market.factor_daily "
                "(ts_code, trade_date, ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60, "
                "boll_mid_bfq, boll_upper_bfq, boll_lower_bfq, "
                "macd_dif_bfq, macd_dea_bfq, macd_bfq, updated_at) "
                "SELECT ma.symbol, f.trading_date, f.ma_bfq_5, f.ma_bfq_10, "
                "f.ma_bfq_20, f.ma_bfq_60, f.boll_mid_bfq, f.boll_upper_bfq, "
                "f.boll_lower_bfq, f.macd_dif_bfq, f.macd_dea_bfq, f.macd_bfq, "
                "f.source_updated_at "
                "FROM public.market_index_factors f "
                "JOIN public.market_assets ma ON ma.id = f.asset_id "
                "ON CONFLICT DO NOTHING")
            conn.commit()
            _mark_done(ckpt, key)
            report["src"] = src
        else:
            report["src"] = "skipped(表缺失)"
    # close 反查填充（目标表显式 market. 前缀；幂等：只补 close IS NULL 行）
    conn.execute(
        "UPDATE market.factor_daily f SET close = d.close "
        "FROM market.instrument_daily d "
        "WHERE f.ts_code = d.ts_code AND f.trade_date = d.trade_date "
        "AND f.close IS NULL")
    conn.commit()
    return report


def _step5_sectors(conn, ckpt: dict) -> dict:
    """概念体系直迁：concept → market.sector（concept_code→sector_code、type 原样）；
    concept_member → market.sector_member（按板块代码分批）。"""
    report = {}
    key = "5:sector"
    if not ckpt.get(key):
        if _table_exists(conn, "public.concept"):
            src = conn.execute("SELECT count(*) FROM public.concept").fetchone()[0]
            conn.execute(
                "INSERT INTO market.sector "
                "(source, sector_code, name, count, exchange, list_date, type) "
                "SELECT source, concept_code, name, count, exchange, list_date, type "
                "FROM public.concept ON CONFLICT DO NOTHING")
            conn.commit()
            _mark_done(ckpt, key)
            report["sector_src"] = src
        else:
            report["sector_src"] = "skipped(表缺失)"
    if _table_exists(conn, "public.concept_member"):
        report["member_src"] = conn.execute(
            "SELECT count(*) FROM public.concept_member").fetchone()[0]
        pairs = conn.execute(
            "SELECT DISTINCT source, concept_code FROM public.concept_member "
            "ORDER BY source, concept_code").fetchall()
        for batch in _iter_batches(pairs, MEMBER_BATCH):
            sources = [p[0] for p in batch]
            key = f"5:member:{batch[0][0]}:{batch[0][1]}"
            if ckpt.get(key):
                continue
            conn.execute(
                "INSERT INTO market.sector_member (source, sector_code, ts_code) "
                "SELECT source, concept_code, ts_code FROM public.concept_member "
                "WHERE (source, concept_code) IN "
                # 显式 ::text[] 类型转换：unnest(unknown) 无类型推断会报
                # "function unnest(unknown) is not unique"（2026-09-13 迁移实测）
                "(SELECT unnest(%(sources)s::text[]), unnest(%(codes)s::text[])) "
                "ON CONFLICT DO NOTHING",
                {"sources": sources, "codes": [p[1] for p in batch]})
            conn.commit()
            _mark_done(ckpt, key)
    return report


def _step6_industry(conn, ckpt: dict) -> dict:
    """行业字典直迁（仅 code→industry_code 改名；name/source 直迁；count 不迁）。"""
    key = "6:industry"
    if ckpt.get(key):
        return {"src": "checkpointed"}
    if not _table_exists(conn, "public.industry_codes"):
        return {"src": "skipped(表缺失)"}
    src = conn.execute("SELECT count(*) FROM public.industry_codes").fetchone()[0]
    conn.execute(
        "INSERT INTO market.industry (source, industry_code, name) "
        "SELECT source, code, name FROM public.industry_codes "
        "ON CONFLICT (source, industry_code) DO NOTHING")
    conn.commit()
    _mark_done(ckpt, key)
    return {"src": src}


def _step7_instruments(conn, ckpt: dict) -> dict:
    """标的表 + 信息表：stock_basic/fund_basic 拆两路、market_assets 先迁、
    eventStudy.assets 后迁（同键 DO NOTHING 先到者胜——000001.SH 名称取
    market_assets 的'上证综指'，与前端目录口径一致）。"""
    report = {}
    subs = (
        ("7:stock_basic", _stock_basic_rows),
        ("7:fund_basic", _fund_basic_rows),
        ("7:market_assets", _market_assets_rows),
        ("7:eventstudy_assets", _eventstudy_assets_rows),
    )
    for key, fn in subs:
        if ckpt.get(key):
            report[key] = "checkpointed"
            continue
        n = fn(conn)
        if n is None:
            report[key] = "skipped(表缺失)"
        else:
            conn.commit()
            _mark_done(ckpt, key)
            report[key] = n
    return report


def _stock_basic_rows(conn):
    if not _table_exists(conn, "public.stock_basic"):
        return None
    n = conn.execute("SELECT count(*) FROM public.stock_basic").fetchone()[0]
    conn.execute(
        "INSERT INTO market.instrument "
        "(ts_code, name, instrument_type, list_status, list_date, delist_date, data_source) "
        "SELECT ts_code, name, 'stock', list_status, list_date, delist_date, 'tushare' "
        "FROM public.stock_basic ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO market.stock_info (ts_code, exchange, market, area) "
        "SELECT ts_code, exchange, market, area FROM public.stock_basic "
        "ON CONFLICT DO NOTHING")
    return n


def _fund_basic_rows(conn):
    if not _table_exists(conn, "public.fund_basic"):
        return None
    n = conn.execute("SELECT count(*) FROM public.fund_basic").fetchone()[0]
    # fund_basic 无 list_status 列 → 基金行 list_status 恒 NULL（不推导不造数据）
    conn.execute(
        "INSERT INTO market.instrument "
        "(ts_code, name, instrument_type, list_status, list_date, delist_date, data_source) "
        "SELECT ts_code, name, 'fund', NULL, list_date, delist_date, 'tushare' "
        "FROM public.fund_basic ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO market.fund_info "
        "(ts_code, management, custodian, trustee, fund_type, invest_type, type, "
        "found_date, due_date, issue_date, issue_amount, m_fee, c_fee, "
        "duration_year, p_value, min_amount, exp_return, benchmark, status, "
        "market, purc_startdate, redm_startdate) "
        "SELECT ts_code, management, custodian, trustee, fund_type, invest_type, "
        "type, found_date, due_date, issue_date, issue_amount, m_fee, c_fee, "
        "duration_year, p_value, min_amount, exp_return, benchmark, status, "
        "market, purc_startdate, redm_startdate FROM public.fund_basic "
        "ON CONFLICT DO NOTHING")
    return n


def _market_assets_rows(conn):
    if not _table_exists(conn, "public.market_assets"):
        return None
    n = conn.execute("SELECT count(*) FROM public.market_assets").fetchone()[0]
    # 仅迁 ts_code=symbol、name、data_source 三列；其余 10 列一律丢弃
    # （平台门控四字段前端写死，currency/timezone 转录进前端目录常量）
    conn.execute(
        "INSERT INTO market.instrument "
        "(ts_code, name, instrument_type, list_status, list_date, delist_date, data_source) "
        "SELECT symbol, name, 'index', NULL, NULL, NULL, data_source "
        "FROM public.market_assets ON CONFLICT DO NOTHING")
    return n


def _eventstudy_assets_rows(conn):
    if not _table_exists(conn, "public.assets"):
        return None
    n = conn.execute("SELECT count(*) FROM public.assets").fetchone()[0]
    # 后写（market_assets 先迁）；同键 000001.SH/000688.SH 以平台口径名称为准（先到者胜）
    conn.execute(
        "INSERT INTO market.instrument "
        "(ts_code, name, instrument_type, list_status, list_date, delist_date, data_source) "
        "SELECT ticker, name, 'index', NULL, NULL, NULL, 'tushare' "
        "FROM public.assets ON CONFLICT DO NOTHING")
    return n


def _step8_report(conn) -> dict:
    """行数核对报告：每步源/目标行数。"""
    pairs = (
        ("instrument_daily", "public.stock_daily"),
        ("adj_factor", "public.adj_factor"),
        ("sector", "public.concept"),
        ("sector_member", "public.concept_member"),
        ("industry", "public.industry_codes"),
    )
    report = {}
    for dst, src in pairs:
        d = conn.execute(f"SELECT count(*) FROM market.{dst}").fetchone()[0]
        if _table_exists(conn, src):
            s = conn.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        else:
            s = "源表已删"
        report[f"{src} → market.{dst}"] = f"源 {s} / 目标 {d}"
    report["market.instrument"] = conn.execute(
        "SELECT count(*) FROM market.instrument").fetchone()[0]
    report["market.instrument_daily 总计"] = conn.execute(
        "SELECT count(*) FROM market.instrument_daily").fetchone()[0]
    return report


# ==================== 主流程 ====================

def _iter_batches(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def run_migration(drop: bool = False) -> dict:
    """按序执行 8 步骤（断点续跑），drop=True 时改为执行幂等 DROP 旧表。"""
    if drop:
        return _run_drop()
    ckpt = _read_checkpoint()
    migrated_at = datetime.now(timezone.utc)
    summary = {}
    with get_connection() as conn:
        conn.execute("SET search_path = public")
        steps = (
            ("1 个股基金日线", _step1_stock_daily, (ckpt, migrated_at)),
            ("2 指数日线合并", _step2_index_daily, (ckpt, migrated_at)),
            ("3 复权因子", _step3_adj_factor, (ckpt,)),
            ("4 技术因子", _step4_factors, (ckpt,)),
            ("5 板块体系", _step5_sectors, (ckpt,)),
            ("6 行业字典", _step6_industry, (ckpt,)),
            ("7 标的+信息表", _step7_instruments, (ckpt,)),
        )
        for name, fn, args in steps:
            try:
                summary[name] = fn(conn, *args)
                logger.info("迁移步骤 %s 完成: %s", name, summary[name])
            except Exception as e:
                logger.error("迁移步骤 %s 失败（不中断后续步骤）: %s", name, e)
                try:
                    conn.rollback()
                except Exception:
                    pass
                summary[name] = f"FAILED: {e}"
        summary["8 行数核对"] = _step8_report(conn)
    return summary


def _backup_legacy_tables() -> None:
    """DROP 前自动备份（决策 6）：本机 pg_dump 可用则 pg_dump -t 各旧表；
    不可用仅告警不阻断（部署环境差异，备份可另行完成）。"""
    import shutil
    import subprocess

    if shutil.which("pg_dump") is None:
        logger.warning("--drop: 本机无 pg_dump，跳过自动备份（请人工备份后再清理）")
        return
    backup_dir = Path("logs/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        f"pg_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_legacy_tables.sql")
    cmd = ["pg_dump", "-U", os.getenv("PG_USER", "liveprofit"),
           "-h", "127.0.0.1", "-p", str(os.getenv("PG_PORT", "5432")),
           "-d", os.getenv("PG_DATABASE", "liveprofit")]
    for table in DROP_TABLES:
        cmd += ["-t", f"public.{table}"]
    try:
        with open(target, "wb") as fh:
            subprocess.run(cmd, stdout=fh, check=True, timeout=1800)
        logger.info("--drop: 旧表已备份至 %s", target)
    except Exception as e:
        logger.warning("--drop: 自动备份失败（不阻断，请人工备份）: %s", e)


def _run_drop() -> dict:
    """--drop：幂等 DROP public 旧表（store 六表 + market_data + industry_codes）。"""
    _backup_legacy_tables()
    with get_connection() as conn:
        conn.execute("SET search_path = public")
        dropped, missing = [], []
        for table in DROP_TABLES:
            if _table_exists(conn, f"public.{table}"):
                conn.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
                dropped.append(table)
            else:
                missing.append(table)
        conn.commit()
        # DROP 完成后清理 checkpoint（旧表已无，重跑迁移无意义）
        if CHECKPOINT_PATH.exists():
            CHECKPOINT_PATH.unlink()
    return {"dropped": dropped, "missing": missing}


def main():
    parser = argparse.ArgumentParser(description="存量迁移：三处旧表 → market schema")
    parser.add_argument("--drop", action="store_true",
                        help="幂等 DROP public 旧表（store 六表 + market_data + industry_codes）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    result = run_migration(drop=args.drop)
    if args.drop:
        logger.info("DROP 完成: 已删 %s / 不存在 %s",
                    result["dropped"], result["missing"])
    else:
        logger.info("迁移结束: %s", json.dumps(result, ensure_ascii=False, indent=2,
                                               default=str))


if __name__ == "__main__":
    main()

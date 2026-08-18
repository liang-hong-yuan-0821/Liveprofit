"""
定时批处理任务主脚本（方案 3.9）

每天早间由 Windows 任务计划程序触发（schtasks，见 scheduler_setup.md），流程：
1. 采集前一日/当日事件（爬虫）→ Redis 待审草稿
2. 拉取最新行情（Provider 层）→ market_data
3. 更新市场上下文 → market_context
4. 向量化新增 approved 事件（bge-m3）
5. 对 PG 中无 Redis 影响草稿的 approved 事件执行事件研究（幂等，草稿过期可重算）

步骤间相互独立，单步失败不影响后续（记录错误日志继续）。
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

from AI.eventStudy.collectors import event_crawler, market_data_collector
from AI.eventStudy.db import market_data_dao
from AI.eventStudy.db.connection import get_connection, init_schema
from AI.eventStudy.processing import event_study, impact_writer, market_context
from AI.eventStudy.processing import event_vectorizer
from AI.eventStudy.collectors.config import TARGET_ASSETS, WINDOW_TYPES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/event_study_daily.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def step_crawl_events(conn):
    """步骤 1：采集事件 → Redis 待审草稿。"""
    events = event_crawler.fetch_events_from_crawler()
    ids = event_crawler.save_pending_events(events, conn=conn)
    logger.info(f"[1/5] 事件采集完成：抓取 {len(events)} 条，新写入待审草稿 {len(ids)} 条")
    return len(ids)


def step_collect_market_data(conn, start_date, end_date):
    """步骤 2：拉取最新行情 → market_data。"""
    result = market_data_collector.collect_index_market_data(
        conn, start_date=start_date, end_date=end_date
    )
    logger.info(f"[2/5] 行情采集完成: {result}")
    return result


def step_update_market_context(conn, start_date, end_date):
    """步骤 3：更新市场环境快照。"""
    n = market_context.update_market_context(conn, start_date, end_date)
    logger.info(f"[3/5] 市场上下文更新完成: {n} 行")
    return n


def step_vectorize(conn):
    """步骤 4：为新增 approved 事件生成向量。"""
    n = event_vectorizer.vectorize_unembedded(conn)
    logger.info(f"[4/5] 事件向量化完成: {n} 条")
    return n


def step_event_study(conn):
    """步骤 5：对无影响草稿的 approved 事件执行事件研究（幂等）。

    跳过规则：
    - Redis 影响草稿存在（含人工未确认）→ 跳过；草稿 TTL 过期后可重算
    - 已全部确认落表（记录数 >= 资产数 × 窗口数）→ 跳过，避免每日重复骚扰
    - 部分确认的事件会重算草稿 → 人工可补确认剩余资产（B5 部分勾选场景）
    注意：trading_day 为空的新事件也在范围内——compute_all_windows
    内部会先自动做 t0 对齐（3.6.1）。
    """
    rows = conn.execute(
        """
        SELECT e.event_id, COALESCE(imp.cnt, 0) AS cnt
        FROM events e
        LEFT JOIN (
            SELECT event_id, COUNT(*) AS cnt FROM event_impacts GROUP BY event_id
        ) imp ON imp.event_id = e.event_id
        WHERE e.status = 'approved'
        """
    ).fetchall()
    n_assets = len(TARGET_ASSETS)  # V1 目标指数数（assets 表未来扩展个股/海外时不抬高阈值）
    full_count = n_assets * len(WINDOW_TYPES)
    done = 0
    for event_id, cnt in rows:
        if cnt >= full_count:
            continue  # 已全部确认落表
        if impact_writer.read_impact_draft(event_id) is not None:
            continue
        try:
            event_study.compute_all_windows(conn, event_id)
            done += 1
        except Exception as e:
            logger.error(f"[5/5] 事件 {event_id} 影响计算失败: {e}")
    logger.info(f"[5/5] 事件研究完成: 新计算 {done} 个事件（共 {len(rows)} 个 approved）")
    return done


def run_daily_job():
    parser = argparse.ArgumentParser(description="事件研究系统每日批处理")
    parser.add_argument("--start-date", default=None, help="行情/上下文起始日期 YYYY-MM-DD，默认 2 年前")
    parser.add_argument("--end-date", default=None, help="截止日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--skip", nargs="*", default=[],
                        choices=["crawl", "market", "context", "vectorize", "study"],
                        help="跳过的步骤")
    args = parser.parse_args()

    end_date = args.end_date or datetime.now().strftime("%Y-%m-%d")
    start_date = args.start_date or (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

    conn = None
    try:
        conn = get_connection()
        if not init_schema(conn):
            logger.error("schema 初始化失败，终止")
            sys.exit(1)
        # 确保 4 个目标资产已初始化
        assets = market_data_dao.list_assets(conn)
        if len(assets) < len(TARGET_ASSETS):
            logger.error("资产种子数据不完整，请检查 schema.sql 执行")
            sys.exit(1)

        steps = [
            ("crawl", lambda: step_crawl_events(conn)),
            ("market", lambda: step_collect_market_data(conn, start_date, end_date)),
            ("context", lambda: step_update_market_context(conn, start_date, end_date)),
            ("vectorize", lambda: step_vectorize(conn)),
            ("study", lambda: step_event_study(conn)),
        ]
        for name, fn in steps:
            if name in args.skip:
                logger.info(f"跳过步骤: {name}")
                continue
            try:
                fn()
            except Exception as e:
                logger.exception(f"步骤 {name} 失败（继续执行后续步骤）")
        logger.info("每日批处理完成")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    run_daily_job()

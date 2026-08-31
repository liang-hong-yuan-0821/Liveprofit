"""
全市场日线本地库 — PostgreSQL 连接与 schema 初始化（方案 3.3.1）

连接配置复用 AI.eventStudy.collectors.config.pg_dsn()（决策 4：不新建配置模块，
store 依赖 eventStudy 配置模块属于"数据层依赖配置层"的既有事实）。
连接规则与 eventStudy 同：psycopg3 直连 + 会话时区显式 Asia/Shanghai。
"""

import logging
from pathlib import Path

import psycopg

from AI.eventStudy.collectors.config import pg_dsn

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection():
    """创建新的 PostgreSQL 连接（autocommit=False，调用方显式 commit）。

    显式设置会话时区为 Asia/Shanghai——与 eventStudy 同规则，
    'YYYY-MM-DD'::timestamptz 字符串解析依赖会话时区，不能依赖部署环境隐式时区。
    """
    conn = psycopg.connect(pg_dsn(), connect_timeout=5)
    try:
        conn.execute("SET TIME ZONE 'Asia/Shanghai'")
    except Exception as e:
        logger.warning(f"设置会话时区失败（沿用服务器默认）: {e}")
    return conn


def init_store_schema(conn=None) -> bool:
    """执行 store/schema.sql 建表（幂等：所有语句 IF NOT EXISTS），返回是否成功。"""
    if not _SCHEMA_PATH.exists():
        logger.error(f"schema.sql 不存在: {_SCHEMA_PATH}")
        return False
    owns = conn is None
    if conn is None:
        conn = get_connection()
    try:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        conn.execute(sql)
        conn.commit()
        logger.info("store schema 初始化/校验完成")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"store schema 初始化失败: {e}")
        return False
    finally:
        if owns:
            conn.close()

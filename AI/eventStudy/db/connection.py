"""
事件研究系统 PostgreSQL 连接管理

- get_connection(): 每调用返回新连接（psycopg3），调用方负责关闭/commit
- init_schema(conn): 执行 db/schema.sql 建表（幂等，可重复执行）
- 数据库不可用时抛 OperationalError，调用方按需降级
"""

import logging
from pathlib import Path

import psycopg

from AI.eventStudy.collectors.config import pg_dsn

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection():
    """创建新的 PostgreSQL 连接（autocommit=False，调用方显式 commit）。

    显式设置会话时区为 Asia/Shanghai——t0 对齐（盘前判断）与
    'YYYY-MM-DD'::timestamptz 字符串解析均依赖会话时区，不能依赖
    部署环境的隐式时区（M3）。
    """
    conn = psycopg.connect(pg_dsn(), connect_timeout=5)
    try:
        conn.execute("SET TIME ZONE 'Asia/Shanghai'")
    except Exception as e:
        logger.warning(f"设置会话时区失败（沿用服务器默认）: {e}")
    return conn


def init_schema(conn=None) -> bool:
    """执行建表脚本（含 CREATE EXTENSION vector + 资产种子数据）。

    幂等：所有语句均为 IF NOT EXISTS / ON CONFLICT DO NOTHING。
    返回是否成功。
    """
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
        logger.info("数据库 schema 初始化/校验完成")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"schema 初始化失败: {e}")
        return False
    finally:
        if owns:
            conn.close()

"""
market schema（liveprofit 库）连接管理 — 证券市场数据库统一方案 3.1.1

- dsn(): 连接串拼装（PG_CONNECTION_STRING 优先，否则 PG_* 分项；host 归一化）
- get_connection(): @contextmanager——yield 后 finally close、异常 rollback、
  会话时区显式 Asia/Shanghai（与 eventStudy/store 同规则）
- init_schema(conn=None): 执行 schema.sql（幂等，无参自建连接、自管关闭）

本模块不 import AI/backend 任何模块（db.instrument 零方向依赖）；配置约定
与 AI.eventStudy.collectors.config 同一套 PG_* 环境变量与默认值（两处读同一
套变量、默认值一致，不合并——避免动事件研究全链测试的 monkeypatch 注入点）。
差异点 = 本模块多 host 归一化（config.py 无）。

注入载体定稿：PG_CONNECTION_STRING 为模块级快照，dsn() 调用时读该模块全局；
测试注入 = 直接赋值 `db.instrument.db.PG_CONNECTION_STRING`（module 级 fixture），
勿用 setenv——快照语义下 setenv 静默失效。
"""

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 连接参数（与 AI.eventStudy.collectors.config 同一套默认值约定）
PG_CONNECTION_STRING = os.getenv("PG_CONNECTION_STRING")
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "liveprofit")
PG_PASSWORD = os.getenv("PG_PASSWORD", "liveprofit123")
PG_DATABASE = os.getenv("PG_DATABASE", "liveprofit")
PG_SSLMODE = os.getenv("PG_SSLMODE", "disable")

# localhost 归一化为 127.0.0.1：Docker 端口代理仅监听 IPv4 loopback，
# psycopg 优先尝试 ::1 被黑洞挂起（2026-09-05 踩坑，backend bootstrap/settings.py 同规则）
_LOOPBACK_HOSTS = ("localhost", "::1")


def dsn() -> str:
    """构建 psycopg 连接串（psycopg 无参连接读不到下划线风格 PG_* 变量，须显式拼装）。"""
    if PG_CONNECTION_STRING:
        return PG_CONNECTION_STRING
    host = PG_HOST
    if host in _LOOPBACK_HOSTS:
        host = "127.0.0.1"
    return (
        f"host={host} port={PG_PORT} dbname={PG_DATABASE} "
        f"user={PG_USER} password={PG_PASSWORD} sslmode={PG_SSLMODE}"
    )


@contextmanager
def get_connection():
    """创建新连接并托管生命周期：退出 with 块即 close（不进连接池），异常时 rollback。

    调用方在 with 块内显式 commit（与 eventStudy/store 同规则）；
    会话时区显式 Asia/Shanghai——'YYYY-MM-DD'::timestamptz 解析依赖会话时区，
    不能依赖部署环境隐式时区。
    """
    conn = psycopg.connect(dsn(), connect_timeout=5)
    try:
        try:
            conn.execute("SET TIME ZONE 'Asia/Shanghai'")
        except Exception as e:
            logger.warning(f"设置会话时区失败（沿用服务器默认）: {e}")
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _exec_schema(conn) -> bool:
    try:
        conn.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        logger.info("market schema 初始化/校验完成")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"market schema 初始化失败: {e}")
        return False


def init_schema(conn=None) -> bool:
    """执行 schema.sql（表 DDL + 31 行行业字典种子，全部幂等），返回是否成功。

    conn=None 时自建连接、自管关闭（先例 connection.py:38 同签名）。
    """
    if not _SCHEMA_PATH.exists():
        logger.error(f"schema.sql 不存在: {_SCHEMA_PATH}")
        return False
    if conn is not None:
        return _exec_schema(conn)
    with get_connection() as conn:
        return _exec_schema(conn)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="db.instrument market schema 管理")
    parser.add_argument(
        "--init-schema", action="store_true", help="执行 schema.sql（幂等，可重复执行）"
    )
    args = parser.parse_args()
    if args.init_schema:
        # run.sh 生产入口依赖此 CLI 形态（失败退出码非 0）
        sys.exit(0 if init_schema() else 1)
    parser.print_help()
    sys.exit(2)

"""
事件研究系统全局配置

集中管理 PG / Redis / 数据源 / 目标资产 / 事件研究参数 / 向量模型 配置，
均支持环境变量覆盖（默认值与 docker-compose 服务对齐）。

独立脚本（daily_job / api / review_app）直接 import 本模块即可完成
.env 加载与连接初始化，不依赖主程序入口。
"""

import os
import logging
from datetime import time

from dotenv import load_dotenv

from AI.config.env_utils import parse_bool_env, parse_int_env, parse_float_env

logger = logging.getLogger(__name__)

load_dotenv()

# ==================== PostgreSQL（事件研究系统主存储） ====================

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = parse_int_env("PG_PORT", 5432)
PG_USER = os.getenv("PG_USER", "liveprofit")
PG_PASSWORD = os.getenv("PG_PASSWORD", "liveprofit123")
PG_DATABASE = os.getenv("PG_DATABASE", "liveprofit")
PG_SSLMODE = os.getenv("PG_SSLMODE", "disable")
# 兼容整串连接串配置方式（优先于分项配置）
PG_CONNECTION_STRING = os.getenv("PG_CONNECTION_STRING", "")


def pg_dsn() -> str:
    """构建 PostgreSQL 连接串（psycopg 格式）。"""
    if PG_CONNECTION_STRING:
        return PG_CONNECTION_STRING
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={PG_DATABASE} "
        f"user={PG_USER} password={PG_PASSWORD} sslmode={PG_SSLMODE}"
    )


# ==================== Redis（事件草稿区 + 审核日志） ====================

REDIS_CONNECTION_STRING = os.getenv("REDIS_CONNECTION_STRING", "")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = parse_int_env("REDIS_PORT", 6379)
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = parse_int_env("REDIS_DB", 0)

# 事件草稿/影响草稿 TTL（秒），过期可重算
IMPACT_DRAFT_TTL = parse_int_env("EVENT_STUDY_IMPACT_DRAFT_TTL", 7 * 86400)
PENDING_DRAFT_TTL = parse_int_env("EVENT_STUDY_PENDING_DRAFT_TTL", 30 * 86400)


def redis_uri() -> str:
    if REDIS_CONNECTION_STRING:
        return REDIS_CONNECTION_STRING
    if REDIS_PASSWORD:
        return f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    return f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"


_redis_client = None


def get_redis_client():
    """懒加载 Redis 客户端（decode_responses=True，JSON 草稿直接读写）。

    连接失败仍返回客户端实例，由调用方经 is_redis_available() 降级。
    """
    global _redis_client
    if _redis_client is None:
        import redis as redis_lib
        _redis_client = redis_lib.from_url(
            redis_uri(), decode_responses=True, socket_connect_timeout=3
        )
        try:
            _redis_client.ping()
        except Exception as e:
            logger.warning(f"Redis 连接失败（草稿区不可用）: {e}")
    return _redis_client


def is_redis_available() -> bool:
    try:
        return bool(get_redis_client().ping())
    except Exception:
        return False


# Redis key 约定（详见方案 3.2 / 4.1 决策 10）
KEY_PENDING_EVENT = "events:pending:{draft_id}"        # 待审核事件草稿
KEY_IMPACT_DRAFT = "event_impacts:draft:{event_id}"    # 影响结果草稿
KEY_REVIEW_LOG = "event_review_log"                    # 审核日志（LPUSH JSON）
KEY_DRAFT_SEQ = "events:draft_seq"                     # 草稿 ID 分配计数器


# ==================== 数据源 Provider ====================

_provider = None


def get_provider():
    """获取当前数据源 Provider 实例（与 AI.dataflows.interface 同规则）。

    LIVEPROFIT_DATA_SOURCE 环境变量控制（默认 tushare）。
    collector 经此接口访问 Provider 层，不直连任何数据源 SDK。
    """
    global _provider
    if _provider is None:
        ds = os.getenv("LIVEPROFIT_DATA_SOURCE", "tushare").lower()
        if ds == "akshare":
            from AI.dataflows.providers.akshare_provider import AKShareProvider
            _provider = AKShareProvider()
        else:
            from AI.dataflows.providers.tushare_provider import TushareProvider
            _provider = TushareProvider()
        logger.info(f"[eventStudy] 数据源: {ds}")
    return _provider


# ==================== 目标资产与市场基准 ====================

# V1 目标指数（决策 2）：上证指数 / 科创50 / 科创100 / 沪深300
TARGET_ASSETS = {
    "000001.SH": "上证指数",
    "000688.SH": "科创50",
    "000698.SH": "科创100",
    "000300.SH": "沪深300",
}

# 市场基准（决策 3）：默认沪深300；沪深300 自身用上证指数替代
MARKET_BENCHMARK = os.getenv("EVENT_STUDY_MARKET_BENCHMARK", "000300.SH")
BENCHMARK_SUBSTITUTE = os.getenv("EVENT_STUDY_BENCHMARK_SUBSTITUTE", "000001.SH")


def get_market_asset(asset_ticker: str) -> str:
    """返回用于事件研究回归的市场指数 ticker（沪深300 自身用上证指数替代）。"""
    if asset_ticker == MARKET_BENCHMARK:
        return BENCHMARK_SUBSTITUTE
    return MARKET_BENCHMARK


# ==================== 事件研究参数（3.6.1） ====================

ESTIMATION_DAYS = parse_int_env("EVENT_STUDY_ESTIMATION_DAYS", 120)
GAP_DAYS = parse_int_env("EVENT_STUDY_GAP_DAYS", 10)
PRE_EVENT_DAYS = parse_int_env("EVENT_STUDY_PRE_EVENT_DAYS", 5)
POST_EVENT_DAYS = parse_int_env("EVENT_STUDY_POST_EVENT_DAYS", 5)

# 方向判定阈值（3.6.1）：|CAR| > 0.5% 且 |t| > 1.96
CAR_DIRECTION_THRESHOLD = parse_float_env("EVENT_STUDY_CAR_THRESHOLD", 0.005)
T_STAT_THRESHOLD = parse_float_env("EVENT_STUDY_T_THRESHOLD", 1.96)

# 污染检查：事件窗口内其他重要性 >= 4 的事件
CONTAMINATION_MIN_IMPORTANCE = parse_int_env("EVENT_STUDY_CONTAMINATION_IMPORTANCE", 4)

# 事件窗口类型（window_type 可读字符串，2.1 说明）
WINDOW_TYPES = ("pre_event_5d", "event_day", "post_event_5d")

# t0 规则（3.6.1 / B3）：盘前（09:30 前）公布 → t0 = 当日；否则 → 下一交易日
# 统一由本配置导出，event_study / market_context 共用（避免重复定义）
PRE_MARKET_CUTOFF = time(9, 30)


# ==================== 向量模型与检索（3.3 / 3.7） ====================

VECTOR_MODEL = os.getenv("EVENT_STUDY_VECTOR_MODEL", "BAAI/bge-m3")
VECTOR_DIM = parse_int_env("EVENT_STUDY_VECTOR_DIM", 1024)
# 国内可经 hf-mirror.com 镜像下载
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")

# 相似检索参数（3.7.1 / B6）
SIMILARITY_MIN = parse_float_env("EVENT_STUDY_SIMILARITY_MIN", 0.5)
SIMILARITY_TOP_K = parse_int_env("EVENT_STUDY_SIMILARITY_TOP_K", 10)
VECTOR_SUPPLEMENT_DISCOUNT = parse_float_env("EVENT_STUDY_VECTOR_DISCOUNT", 0.5)
TEMPLATE_WEIGHT = 1.0


# ==================== 爬虫目标（3.1.1 / C3） ====================

CRAWLER_CONFIG = {
    # 主源：财联社电报 / 金十数据快讯
    "cls_telegraph": {
        # v1 接口（参数排序 → SHA-1 → MD5 签名，支持深度分页）
        "url": "https://www.cls.cn/v1/roll/get_roll_list",
        # 备用接口（live 刷新用，无需签名）
        "nodeapi_url": "https://www.cls.cn/nodeapi/updateTelegraphList",
        "sv": "8.4.6",
        "enabled": parse_bool_env("CRAWLER_CLS_ENABLED", True),
    },
    "jin10_flash": {
        "url": "https://flash-api.jin10.com/get_flash_list",
        "enabled": parse_bool_env("CRAWLER_JIN10_ENABLED", True),
    },
    # 备源：新浪财经 7x24 / 东方财富快讯
    "sina_724": {
        "url": "https://zhibo.sina.com.cn/api/zhibo/feed",
        "enabled": parse_bool_env("CRAWLER_SINA_ENABLED", False),
    },
    "eastmoney_flash": {
        "url": "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
        "enabled": parse_bool_env("CRAWLER_EM_ENABLED", False),
    },
}

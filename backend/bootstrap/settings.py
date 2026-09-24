"""Settings 组合根：core / api / worker / dispatcher / market_ingestion / event_study 子模型。

- API/Worker/Dispatcher 复用同一 Settings 定义，但每个进程只校验其实际启用能力（fail-fast）。
- Secret（API Key、含密码的 URL）使用 SecretStr，永不进入日志、OpenAPI 或 repr。
- 兼容既有内核环境变量：LIVEPROFIT_*（LLM 配置）、PG_* / REDIS_*（本地基础设施），
  .env 加载遵循 main.py 约定（override=False，系统环境变量优先）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file() -> None:
    """加载项目根 .env（override=False 保持与 main.py / AI 内核一致）。"""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


_load_env_file()


class SettingsValidationError(ValueError):
    """进程启动校验失败（fail-fast）。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class CoreSettings(BaseSettings):
    """平台核心与 LLM 透传配置（与 AI/default_config.py 的既有环境变量一一对应）。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_", extra="ignore")

    # 运行环境：local = 宿主进程（loopback 强制）；container = Compose 内部（可 0.0.0.0 监听）
    env: Literal["local", "container"] = "local"

    # 平台基础设施连接；未显式配置时回退到既有 PG_* / REDIS_* 变量（见 resolved_* 方法）
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None

    # LLM 透传（Secret 不落日志/OpenAPI）
    api_key: SecretStr = SecretStr("")
    base_url: str = "https://api.openai.com/v1"
    # 注意：pydantic-settings 对带 alias 的字段不叠加 env_prefix，
    # alias 必须写完整环境变量名（与 AI/default_config.py / README / .env 的既有约定一致）
    quick_think_llm: str = Field(default="gpt-4o-mini", validation_alias="LIVEPROFIT_QUICK_MODEL")
    deep_think_llm: str = Field(default="gpt-4o", validation_alias="LIVEPROFIT_DEEP_MODEL")
    quick_temperature: float = 0.7
    deep_temperature: float = 0.3
    max_tokens: int = 8192
    data_source: str = "tushare"

    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"

    # 业务级重试（§2.4：可重试错误由 fail_or_retry 唯一决策，Dramatiq 不做业务重试）
    max_retry_attempts: int = 3  # 含首次执行，共 3 次尝试
    retry_base_delay_seconds: int = 60  # 指数退避基数：delay = base * 2^(attempt-1)
    recovery_grace_seconds: int = 60  # 租约恢复宽限期：仅处理 lease_ttl + grace 之后仍未续租的任务

    # 产物保留与限额（§2.4 / §5.1 P-6）
    artifact_root: Path = Path("var/runs")
    research_dataset_root: Path = Path("var/research/datasets")
    artifact_retention_days: int = 90
    max_artifact_bytes_per_task: int = 100 * 1024 * 1024  # 100 MB
    max_artifact_file_bytes: int = 10 * 1024 * 1024  # 10 MB
    artifact_high_water_bytes: int = 20 * 1024**3  # 20 GB

    # 执行调用日志根目录（任务执行调用日志方案）：worker 写入与 API 读取的确定性基座，
    # 相对路径经 resolve_execution_logs_root 按 PROJECT_ROOT 解析（不得按进程 CWD）
    execution_logs_root: Path = Path("logs")

    # SSE Stream 保留（§2.4）
    stream_maxlen: int = 1000
    stream_retention_seconds: int = 7 * 86400

    def resolved_database_url(self) -> str | None:
        """显式 LIVEPROFIT_DATABASE_URL 优先，否则由既有 PG_* 变量拼装（本地开发）。"""
        if self.database_url is not None:
            return self.database_url.get_secret_value() or None
        pg_host = os.getenv("PG_HOST")
        if not pg_host:
            return None
        pg_user = os.getenv("PG_USER", "liveprofit")
        pg_password = os.getenv("PG_PASSWORD", "")
        pg_port = os.getenv("PG_PORT", "5432")
        pg_database = os.getenv("PG_DATABASE", "liveprofit")
        sslmode = os.getenv("PG_SSLMODE")
        # localhost 归一化为 127.0.0.1：Docker 端口代理仅监听 IPv4 loopback，
        # 避免 psycopg 优先尝试 ::1 被黑洞挂起（2026-09-05 踩坑）
        if pg_host in ("localhost", "::1"):
            pg_host = "127.0.0.1"
        from urllib.parse import quote
        url = f"postgresql+psycopg://{quote(pg_user, safe='')}:{quote(pg_password, safe='')}@{pg_host}:{pg_port}/{quote(pg_database, safe='')}"
        return f"{url}?sslmode={sslmode}" if sslmode else url

    def resolved_redis_url(self) -> str | None:
        """显式 LIVEPROFIT_REDIS_URL 优先，否则 REDIS_CONNECTION_STRING / REDIS_* 拼装。"""
        if self.redis_url is not None:
            return self.redis_url.get_secret_value() or None
        conn_str = os.getenv("REDIS_CONNECTION_STRING")
        if conn_str:
            return _normalize_loopback_host(conn_str)
        redis_host = os.getenv("REDIS_HOST")
        if not redis_host:
            return None
        redis_password = os.getenv("REDIS_PASSWORD", "")
        redis_port = os.getenv("REDIS_PORT", "6379")
        redis_db = os.getenv("REDIS_DB", "0")
        if redis_host in ("localhost", "::1"):
            redis_host = "127.0.0.1"
        auth = f":{redis_password}@" if redis_password else ""
        return f"redis://{auth}{redis_host}:{redis_port}/{redis_db}"

    def resolved_market_dsn(self) -> str | None:
        """平台行情连接与平台数据库同库；凭据转义交给 psycopg。"""
        url = self.resolved_database_url()
        if url is None:
            return None
        return database_url_to_dsn(url)


def database_url_to_dsn(url: str) -> str:
    """SQLAlchemy PostgreSQL URL → psycopg conninfo，保留连接选项。"""
    from psycopg.conninfo import make_conninfo
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("市场数据库必须使用 PostgreSQL")
    values = dict(parsed.query)
    values.update({k: v for k, v in {
        "host": "127.0.0.1" if parsed.host in ("localhost", "::1") else parsed.host,
        "port": parsed.port, "dbname": parsed.database,
        "user": parsed.username, "password": parsed.password,
    }.items() if v is not None})
    return make_conninfo(**values)


def resolve_execution_logs_root(core: CoreSettings) -> Path:
    """执行日志根目录解析单一出口：相对路径按 PROJECT_ROOT（仓库根）解析为绝对路径。

    worker 进程（写入）与 API 进程（读取）是两个进程，CWD 可能不同——按 cwd 解析
    会导致写入目录与读取目录分叉、端点恒空。execution_logs 路由（读）、wiring
    注入（写）、删除清理三处统一调用本函数，禁止各自解析。
    """
    root = Path(core.execution_logs_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root


def _normalize_loopback_host(url: str) -> str:
    """连接串中的 localhost/::1 归一化为 127.0.0.1（Docker 端口代理仅监听 IPv4 loopback）。"""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.hostname in ("localhost", "::1"):
        host = "127.0.0.1"
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
        if parts.username or parts.password:
            # 注意 :pass@host 形式下 username 为空串但 password 非空，不得丢密码
            auth = (parts.username or "") + (f":{parts.password}" if parts.password else "")
            netloc = f"{auth}@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


class ApiSettings(BaseSettings):
    """API 进程配置。local 环境强制 loopback 监听（§3.1.1 loopback local-only）。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_API_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1


class WorkerSettings(BaseSettings):
    """Dramatiq Worker 配置（§3.1.1 固定基线：单进程单线程 + 租约/心跳不变式）。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_WORKER_", extra="ignore")

    dramatiq_processes: int = 1
    dramatiq_threads: int = 1
    lease_ttl_seconds: int = 120
    heartbeat_interval_seconds: int = 30
    max_attempt_runtime_seconds: int = 3600
    global_llm_concurrency: int = 1


class DispatcherSettings(BaseSettings):
    """Outbox Dispatcher 常驻进程配置（§3.1.1）。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_DISPATCH_", extra="ignore")

    poll_interval_seconds: float = 2.0
    batch_size: int = 20
    recovery_interval_seconds: int = 60
    idle_backoff_seconds: float = 5.0


class DailyResearchSettings(BaseSettings):
    """每日投研准入与原文采集节奏；调度只由平台 Dispatcher 执行。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_DAILY_RESEARCH_", extra="ignore")

    enabled: bool = True
    schedule_check_seconds: int = Field(default=30, gt=0)
    news_capture_interval_seconds: int = Field(default=300, gt=0)
    news_analysis_interval_seconds: int = Field(default=1800, gt=0)
    max_news_per_run: int = Field(default=100, ge=1, le=1000)


class MarketIngestionSettings(BaseSettings):
    """行情/热点/宏观信息采集进程配置（二期启用，仅占位）。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_INGESTION_", extra="ignore")

    poll_interval_seconds: int = 300


class EventStudySettings(BaseSettings):
    """事件研究有界执行器配置（§3.1.5，均须为正）。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_EVENT_STUDY_", extra="ignore")

    max_workers: int = 2
    max_queue: int = 2
    timeout_seconds: int = 30


class MarketRefreshSettings(BaseSettings):
    """六组日线补齐策略；环境变量统一 MARKET_REFRESH_*。"""

    model_config = SettingsConfigDict(env_prefix="MARKET_REFRESH_", extra="ignore")

    enabled: bool = True
    auto_enabled: bool = True
    key_prefix: str = "liveprofit:market-refresh:"
    broker_namespace: str = "dramatiq"
    publish_lag_seconds: dict[str, int] = Field(default_factory=lambda: {
        "CN_INDEX_BARS": 18000, "CN_INDEX_FACTORS": 18000,
        "US_INDEX_BARS": 14400, "KR_INDEX_BARS": 14400,
        "CN_STOCK_DAILY": 18000, "CN_SECTOR_DAILY": 18000,
    })
    window_sessions: int = Field(default=3, ge=1)
    check_interval_seconds: int = Field(default=60, gt=0)
    coverage_check_interval_seconds: int = Field(default=300, gt=0)
    coverage_ttl_seconds: int = Field(default=60, gt=0)
    auto_max_attempts: int = Field(default=3, ge=1)
    rolling_max_attempts: int = Field(default=6, ge=1)
    rolling_window_seconds: int = Field(default=86400, gt=0)
    auto_retry_delays_seconds: tuple[int, ...] = (900, 3600)
    manual_cooldown_seconds: int = Field(default=300, gt=0)
    max_dispatches: int = Field(default=3, ge=1)
    dispatch_retry_delays_seconds: tuple[int, ...] = (300, 900)
    lock_retry_seconds: int = Field(default=30, gt=0)
    lease_ttl_seconds: int = Field(default=180, gt=0)
    heartbeat_interval_seconds: int = Field(default=15, gt=0)
    worker_ttl_seconds: int = Field(default=45, gt=0)
    child_timeout_seconds: int = Field(default=5400, gt=0)
    actor_timeout_seconds: int = Field(default=5700, gt=0)
    terminate_grace_seconds: int = Field(default=10, gt=0)
    kill_grace_seconds: int = Field(default=20, gt=0)
    terminal_ttl_seconds: int = Field(default=86400, gt=0)
    retired_attempts_ttl_seconds: int = Field(default=604800, gt=0)
    changed_ttl_seconds: int = Field(default=604800, gt=0)
    budget_ttl_seconds: int = Field(default=172800, gt=0)
    factor_cooldown_seconds: int = Field(default=900, gt=0)
    progress_interval_seconds: int = Field(default=5, gt=0)

    @model_validator(mode="after")
    def validate_bounds(self):
        resources = {"CN_INDEX_BARS", "CN_INDEX_FACTORS", "US_INDEX_BARS",
                     "KR_INDEX_BARS", "CN_STOCK_DAILY", "CN_SECTOR_DAILY"}
        if set(self.publish_lag_seconds) != resources or any(v < 0 for v in self.publish_lag_seconds.values()):
            raise ValueError("publish_lag_seconds 必须包含六资源的非负发布缓冲")
        if not self.key_prefix or not self.key_prefix.endswith(":") or not self.broker_namespace:
            raise ValueError("业务前缀必须以冒号结尾，Broker namespace 不得为空")
        if any(c in self.key_prefix + self.broker_namespace for c in "*?[]"):
            raise ValueError("命名空间不得包含 Redis glob 字符")
        if self.heartbeat_interval_seconds * 2 >= min(self.lease_ttl_seconds, self.worker_ttl_seconds):
            raise ValueError("心跳间隔必须小于租约和 Worker TTL 的一半")
        if self.actor_timeout_seconds <= self.child_timeout_seconds + self.terminate_grace_seconds + self.kill_grace_seconds:
            raise ValueError("actor 超时必须大于子进程超时加回收宽限")
        if self.budget_ttl_seconds < self.rolling_window_seconds:
            raise ValueError("预算 TTL 不得短于滚动窗口")
        for attempts, delays in ((self.auto_max_attempts, self.auto_retry_delays_seconds),
                                 (self.max_dispatches, self.dispatch_retry_delays_seconds)):
            if len(delays) < attempts - 1 or any(delay <= 0 for delay in delays):
                raise ValueError("重试延迟须为正，且覆盖全部重试轮次")
        return self


class Settings(BaseSettings):
    """顶层配置聚合；每个进程通过 validate_for_process() 只校验其启用能力。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_", extra="ignore")

    core: CoreSettings = Field(default_factory=CoreSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    dispatcher: DispatcherSettings = Field(default_factory=DispatcherSettings)
    daily_research: DailyResearchSettings = Field(default_factory=DailyResearchSettings)
    market_ingestion: MarketIngestionSettings = Field(default_factory=MarketIngestionSettings)
    market_refresh: MarketRefreshSettings = Field(default_factory=MarketRefreshSettings)
    event_study: EventStudySettings = Field(default_factory=EventStudySettings)

    def _collect(self, errors: list[str], ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    def validate_for_process(self, process: Literal["api", "worker", "dispatcher", "market_ingestion", "market_worker"]) -> None:
        """fail-fast 校验：只校验该进程实际启用的能力。"""
        if process not in {"api", "worker", "dispatcher", "market_ingestion", "market_worker"}:
            raise SettingsValidationError(["未知平台进程类型"])
        errors: list[str] = []

        needs_db = process in {"api", "worker", "dispatcher", "market_ingestion", "market_worker"}
        # Dispatcher 也需要 Redis：Outbox 确认后发布 queued 事件 + Dramatiq Broker
        needs_redis = process in {"api", "worker", "dispatcher", "market_ingestion", "market_worker"}

        if needs_db:
            self._collect(errors, self.core.resolved_database_url() is not None,
                          "缺少 DATABASE_URL（LIVEPROFIT_DATABASE_URL 或 PG_* 变量）")
        if needs_redis:
            self._collect(errors, self.core.resolved_redis_url() is not None,
                          "缺少 REDIS_URL（LIVEPROFIT_REDIS_URL 或 REDIS_CONNECTION_STRING/REDIS_* 变量）")

        if process == "api":
            host = self.api.host
            is_loopback = host in ("127.0.0.1", "localhost", "::1")
            if self.core.env == "local":
                self._collect(errors, is_loopback,
                              "local 环境 API 仅允许 loopback 监听（127.0.0.1/localhost/::1），"
                              "LAN 访问属于未来独立认证/TLS/CORS 方案")
            self._collect(errors, self.api.port > 0, "api.port 必须为正")
            self._collect(errors, self.api.workers >= 1, "api.workers 必须 >= 1")
            self._collect(errors, self.event_study.max_workers > 0
                          and self.event_study.max_queue > 0 and self.event_study.timeout_seconds > 0,
                          "event_study.max_workers/max_queue/timeout_seconds 均须为正")

        if process == "worker":
            self._collect(errors, self.worker.dramatiq_processes >= 1, "dramatiq_processes 必须 >= 1")
            self._collect(errors, self.worker.dramatiq_threads >= 1, "dramatiq_threads 必须 >= 1")
            self._collect(errors, self.worker.lease_ttl_seconds > 0, "lease_ttl_seconds 必须为正")
            self._collect(errors, self.worker.heartbeat_interval_seconds > 0, "heartbeat_interval_seconds 必须为正")
            self._collect(errors, self.worker.heartbeat_interval_seconds < self.worker.lease_ttl_seconds / 2,
                          "heartbeat_interval_seconds 必须 < lease_ttl_seconds / 2")
            self._collect(errors, self.worker.max_attempt_runtime_seconds > 0,
                          "max_attempt_runtime_seconds 必须为正")
            self._collect(errors, self.worker.global_llm_concurrency >= 1, "global_llm_concurrency 必须 >= 1")

        if process == "dispatcher":
            self._collect(errors, self.dispatcher.poll_interval_seconds > 0, "poll_interval_seconds 必须为正")
            self._collect(errors, self.dispatcher.batch_size >= 1, "batch_size 必须 >= 1")
            self._collect(errors, self.dispatcher.recovery_interval_seconds > 0, "recovery_interval_seconds 必须为正")
            self._collect(errors, self.daily_research.schedule_check_seconds > 0,
                          "daily_research.schedule_check_seconds 必须为正")
            self._collect(errors, self.daily_research.news_capture_interval_seconds > 0,
                          "daily_research.news_capture_interval_seconds 必须为正")
            self._collect(errors, self.daily_research.news_analysis_interval_seconds > 0,
                          "daily_research.news_analysis_interval_seconds 必须为正")

        if process == "market_ingestion":
            self._collect(errors, self.market_ingestion.poll_interval_seconds > 0, "poll_interval_seconds 必须为正")

        if errors:
            raise SettingsValidationError(errors)

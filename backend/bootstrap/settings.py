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
from pydantic import Field, SecretStr
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
        url = f"postgresql+psycopg://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"
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


class Settings(BaseSettings):
    """顶层配置聚合；每个进程通过 validate_for_process() 只校验其启用能力。"""

    model_config = SettingsConfigDict(env_prefix="LIVEPROFIT_", extra="ignore")

    core: CoreSettings = Field(default_factory=CoreSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    dispatcher: DispatcherSettings = Field(default_factory=DispatcherSettings)
    market_ingestion: MarketIngestionSettings = Field(default_factory=MarketIngestionSettings)
    event_study: EventStudySettings = Field(default_factory=EventStudySettings)

    def _collect(self, errors: list[str], ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    def validate_for_process(self, process: Literal["api", "worker", "dispatcher", "market_ingestion"]) -> None:
        """fail-fast 校验：只校验该进程实际启用的能力。"""
        errors: list[str] = []

        needs_db = process in {"api", "worker", "dispatcher", "market_ingestion"}
        # Dispatcher 也需要 Redis：Outbox 确认后发布 queued 事件 + Dramatiq Broker
        needs_redis = process in {"api", "worker", "dispatcher", "market_ingestion"}

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

        if process == "market_ingestion":
            self._collect(errors, self.market_ingestion.poll_interval_seconds > 0, "poll_interval_seconds 必须为正")

        if errors:
            raise SettingsValidationError(errors)

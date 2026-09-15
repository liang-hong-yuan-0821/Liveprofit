"""Alembic 环境：同步引擎 + 平台表 metadata。

数据库 URL 解析顺序：alembic -x db_url=... 覆盖 > Settings（LIVEPROFIT_DATABASE_URL / PG_* 回退）。
只演进平台新增表；事件研究既有表不属于 target_metadata，迁移不会触碰。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from backend.bootstrap.settings import CoreSettings
from backend.shared.db import Base

# 导入全部平台 ORM 模型以填充 Base.metadata
from backend.modules.analysis.infrastructure import models as analysis_models  # noqa: F401
from backend.modules.event_study.infrastructure import models as event_study_models  # noqa: F401
from backend.modules.investment_workspace.infrastructure import models as workspace_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return x_args["db_url"]
    url = CoreSettings().resolved_database_url()
    if not url:
        raise RuntimeError("缺少数据库 URL：请配置 LIVEPROFIT_DATABASE_URL / PG_* 或 alembic -x db_url=...")
    return url


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连接数据库。"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：同步引擎执行（迁移为一次性 DDL，不需要 async）。"""
    engine = create_engine(_resolve_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""统一响应信封：{data, meta}；列表续页游标只在 meta.next_cursor。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

SCHEMA_VERSION = "v1"

T = TypeVar("T")


class EnvelopeMeta(BaseModel):
    request_id: str = Field(description="请求标识（X-Trace-ID）")
    schema_version: str = SCHEMA_VERSION
    next_cursor: str | None = Field(default=None, description="不透明续页游标；无下一页为 null")


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: EnvelopeMeta


class DeleteResultData(BaseModel):
    deleted: bool = True
    resource_id: str = Field(description="被删除资源 UUID")

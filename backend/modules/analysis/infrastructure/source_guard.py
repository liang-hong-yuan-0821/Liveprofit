"""策略源码泄漏防护（plan 4.2.1）：artifact/checkpoint/SSE/log 序列化边界共用 guard。

递归拒绝：完整 execution_snapshot 对象、键名（大小写折叠）为 source_code /
execution_snapshot 的任意值、完整 64 位 source_hash；任意字符串拒绝等于或包含
完整源码（含 JSON 解码/转义后文本）。命中抛 ArtifactSourceLeakError
（FatalAnalysisError → classify_error 落不可重试任务失败，并拒绝发布）。
允许固定 12 位不可逆 source_hash_prefix 供报告审计。
"""

from __future__ import annotations

from backend.modules.analysis.application.errors import ArtifactSourceLeakError

_SENSITIVE_KEYS = frozenset({"source_code", "execution_snapshot"})


def assert_no_source_leak(
    value,
    forbidden_source_code: str | None,
    *,
    forbidden_source_hash: str | None = None,
    context: str = "payload",
) -> None:
    """递归检查 value 是否承载源码/敏感键/完整散列；命中抛 ArtifactSourceLeakError。"""
    if forbidden_source_code is None and forbidden_source_hash is None:
        return
    if isinstance(value, str):
        if forbidden_source_code and forbidden_source_code in value:
            raise ArtifactSourceLeakError(f"{context} 检出策略源码泄漏")
        if forbidden_source_hash and value == forbidden_source_hash:
            raise ArtifactSourceLeakError(f"{context} 检出完整 source_hash")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                raise ArtifactSourceLeakError(f"{context} 检出敏感键 {key!r}")
            assert_no_source_leak(
                item,
                forbidden_source_code,
                forbidden_source_hash=forbidden_source_hash,
                context=context,
            )
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            assert_no_source_leak(
                item,
                forbidden_source_code,
                forbidden_source_hash=forbidden_source_hash,
                context=context,
            )
        return
    # 其余标量（int/float/bool/None）不承载源码

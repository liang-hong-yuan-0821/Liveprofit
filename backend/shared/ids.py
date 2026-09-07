"""ID 生成。"""

from __future__ import annotations

import secrets
import uuid


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def new_token() -> str:
    """租约/分发租约 token：高熵随机串，不泄露任何信息。"""
    return secrets.token_hex(32)

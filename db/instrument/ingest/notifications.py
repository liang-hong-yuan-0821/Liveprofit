"""Optional Redis invalidation for standalone maintenance CLIs (no backend)."""

from __future__ import annotations

import logging
import os
from urllib.parse import quote
from uuid import uuid4

logger = logging.getLogger(__name__)


def market_changed_notifier_from_env():
    """Resolve explicit platform URL first, then legacy Redis environment.

    No URL/host means no configured notification transport. PG facts remain
    authoritative and platform coverage checks discover the committed changes.
    """
    url = os.getenv("LIVEPROFIT_REDIS_URL") or os.getenv("REDIS_CONNECTION_STRING")
    if not url and os.getenv("REDIS_HOST"):
        password = os.getenv("REDIS_PASSWORD", "")
        auth = f":{quote(password, safe='')}@" if password else ""
        url = (f"redis://{auth}{os.environ['REDIS_HOST']}:"
               f"{os.getenv('REDIS_PORT', '6379')}/{os.getenv('REDIS_DB', '0')}")
    prefix = os.getenv("MARKET_REFRESH_KEY_PREFIX", "liveprofit:market-refresh:")
    ttl = int(os.getenv("MARKET_REFRESH_CHANGED_TTL_SECONDS", "604800"))

    def changed(resource):
        if not url:
            return
        try:
            from redis import Redis
            with Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5) as client, client.pipeline() as pipe:
                pipe.set(f"{prefix}changed:{resource}", uuid4().hex, ex=ttl)
                pipe.delete(f"{prefix}coverage:{resource}")
                pipe.execute()
        except Exception:  # noqa: BLE001 - notifications must not undo committed PG facts
            logger.debug("market invalidation unavailable: %s", resource)
    return changed

"""Capture exchange-hosted rule bytes for diagnosis, without certifying meaning.

TLS origin and a content checksum show what this process fetched. They do not
prove the document's historical publication time or a per-security reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPException
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_EXCHANGE_DOMAINS = ("sse.com.cn", "szse.cn", "bse.cn")
_MEDIA_TYPES = {
    "text/html", "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}
_MAX_BYTES = 8 * 1024 * 1024


class RuleArtifactUnavailable(ValueError):
    pass


def exchange_url(value: str) -> str:
    """Accept only HTTPS URLs under a Chinese exchange's own DNS domain."""
    if not isinstance(value, str) or not value:
        raise RuleArtifactUnavailable("official exchange URL required")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise RuleArtifactUnavailable("invalid official exchange URL") from None
    if (parts.scheme.lower() != "https" or not host or parts.username or parts.password
            or port not in (None, 443)
            or not any(host == domain or host.endswith("." + domain)
                       for domain in _EXCHANGE_DOMAINS)):
        raise RuleArtifactUnavailable("URL is outside official exchange HTTPS domains")
    return urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))


class _ExchangeRedirects(HTTPRedirectHandler):
    def __init__(self) -> None:
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops += 1
        if self.hops > 5:
            raise RuleArtifactUnavailable("official artifact redirect limit exceeded")
        target = exchange_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, target)


@dataclass(frozen=True)
class OfficialRuleArtifact:
    requested_uri: str
    source_uri: str
    media_type: str
    raw_content: bytes
    content_sha256: str
    fetched_at: datetime


def fetch_exchange_rule_artifact(url: str, *, opener=None,
                                 max_bytes: int = _MAX_BYTES) -> OfficialRuleArtifact:
    """Fetch bounded original bytes; publication date remains unverified."""
    requested = exchange_url(url)
    if type(max_bytes) is not int or not 0 < max_bytes <= _MAX_BYTES:
        raise ValueError("invalid artifact byte limit")
    client = opener if opener is not None else build_opener(_ExchangeRedirects())
    request = Request(requested, headers={
        "Accept": "text/html,application/pdf,application/msword,application/octet-stream,*/*",
        "Accept-Encoding": "identity",
        "User-Agent": "Liveprofit-rule-evidence/1.0",
    })
    try:
        with client.open(request, timeout=15) as response:
            final = exchange_url(response.geturl())
            media_type = response.headers.get_content_type().lower()
            if media_type not in _MEDIA_TYPES:
                raise RuleArtifactUnavailable("unsupported official artifact media type")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise RuleArtifactUnavailable("compressed official artifact is unsupported")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                raise RuleArtifactUnavailable("official artifact exceeds byte limit")
            raw = response.read(max_bytes + 1)
    except RuleArtifactUnavailable:
        raise
    except (OSError, ValueError, HTTPException) as exc:
        raise RuleArtifactUnavailable(f"official artifact fetch failed: {type(exc).__name__}") from exc
    if not raw or len(raw) > max_bytes or (length is not None and len(raw) != int(length)):
        raise RuleArtifactUnavailable("official artifact is empty, truncated or exceeds byte limit")
    return OfficialRuleArtifact(
        requested_uri=requested, source_uri=final, media_type=media_type,
        raw_content=raw, content_sha256=sha256(raw).hexdigest(),
        fetched_at=datetime.now(timezone.utc),
    )

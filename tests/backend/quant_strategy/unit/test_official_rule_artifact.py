# test-catalog-begin
# {
#   "purpose": "量化策略 / official_rule_artifact（品种规则）：Offline tests for bounded official-origin artifact capture.",
#   "keywords": [
#     "量化策略",
#     "品种规则",
#     "official_rule_artifact",
#     "rule"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/infrastructure/official_rule_artifact.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Offline tests for bounded official-origin artifact capture."""

from email.message import Message
from hashlib import sha256
from http.client import IncompleteRead
from urllib.request import Request

import pytest

from backend.modules.quant_strategy.infrastructure.official_rule_artifact import (
    RuleArtifactUnavailable, _ExchangeRedirects, exchange_url,
    fetch_exchange_rule_artifact,
)


class _Response:
    def __init__(self, *, url="https://www.sse.com.cn/rules/current.pdf", body=b"%PDF-source",
                 content_type="application/pdf", encoding="identity", content_length=None):
        self.url, self.body = url, body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Encoding"] = encoding
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class _Opener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        assert timeout == 15
        return self.response


def test_official_pdf_capture_preserves_exact_bytes_and_final_url():
    body = b"%PDF-1.7\r\n\x00original"
    opener = _Opener(_Response(body=body))
    result = fetch_exchange_rule_artifact(
        "https://www.sse.com.cn/rules/current.pdf#view=1", opener=opener,
    )
    assert result.requested_uri == "https://www.sse.com.cn/rules/current.pdf"
    assert result.source_uri == "https://www.sse.com.cn/rules/current.pdf"
    assert result.media_type == "application/pdf"
    assert result.raw_content == body
    assert result.content_sha256 == sha256(body).hexdigest()
    assert result.fetched_at.tzinfo is not None
    assert opener.request.get_header("Accept-encoding") == "identity"


@pytest.mark.parametrize("url", [
    "http://www.sse.com.cn/rules", "https://www.sse.com.cn.evil.test/rules",
    "https://evil.test@www.sse.com.cn/rules", "https://127.0.0.1/rules",
    "https://www.sse.com.cn:444/rules", "file:///tmp/rules",
])
def test_non_exchange_or_unsafe_url_is_rejected(url):
    with pytest.raises(RuleArtifactUnavailable):
        exchange_url(url)


def test_redirect_cannot_escape_exchange_https_or_loop_forever():
    handler = _ExchangeRedirects()
    req = Request("https://www.szse.cn/rules/current")
    with pytest.raises(RuleArtifactUnavailable):
        handler.redirect_request(req, None, 302, "Moved", {}, "https://evil.test/rule")
    for _ in range(4):
        redirected = handler.redirect_request(req, None, 302, "Moved", {}, "/rules/latest")
        assert redirected.full_url == "https://www.szse.cn/rules/latest"
    with pytest.raises(RuleArtifactUnavailable, match="limit"):
        handler.redirect_request(req, None, 302, "Moved", {}, "/rules/again")


@pytest.mark.parametrize("response", [
    _Response(url="https://evil.test/rule"),
    _Response(body=b""),
    _Response(body=b"12345", content_length=5),
    _Response(body=b"abc", content_length=4),
    _Response(encoding="gzip"),
    _Response(content_type="image/png"),
])
def test_bad_final_origin_body_size_encoding_and_media_are_rejected(response):
    with pytest.raises(RuleArtifactUnavailable):
        fetch_exchange_rule_artifact("https://www.sse.com.cn/rules/a", opener=_Opener(response),
                                     max_bytes=4)


def test_exact_byte_limit_is_accepted():
    result = fetch_exchange_rule_artifact(
        "https://docs.static.szse.cn/rules/r.pdf",
        opener=_Opener(_Response(url="https://docs.static.szse.cn/rules/r.pdf",
                                 body=b"1234", content_length=4)), max_bytes=4,
    )
    assert result.raw_content == b"1234"


def test_truncated_http_response_exception_uses_fetch_error_contract():
    class Broken(_Response):
        def read(self, size):
            raise IncompleteRead(b"%PDF", 20)

    with pytest.raises(RuleArtifactUnavailable, match="fetch failed: IncompleteRead"):
        fetch_exchange_rule_artifact("https://www.sse.com.cn/rules/a.pdf",
                                     opener=_Opener(Broken()))

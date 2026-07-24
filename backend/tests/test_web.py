"""web_fetch over an httpx.MockTransport — deterministic, no real network.

The SSRF checks are exhaustively tested in test_ssrf.py; here the resolver is
stubbed to accept every hostname (all-public), except where a block is the whole
point, and the transport returns canned responses so redirects, the output cap,
status handling and HTML-stripping are deterministic.
"""

from __future__ import annotations

import httpx

from jarvis_backend.security.permissions import ASK, Decision
from jarvis_backend.tools import web
from jarvis_backend.tools.registry import Registry
from jarvis_backend.tools.web import MAX_FETCH_BYTES


class AllowAll:
    async def check(self, name, risk, arguments, context):
        return Decision.allow()


def _all_public():
    async def resolve(host, port):
        return ["93.184.216.34"]

    return resolve


def _client_factory(handler):
    def make():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    return make


def _registry(handler, *, resolver=None, gate=None) -> Registry:
    r = Registry(gate or AllowAll())
    web.register(r, client_factory=_client_factory(handler), resolver=resolver or _all_public())
    return r


async def _fetch(handler, url="http://example.com/", *, resolver=None):
    return await _registry(handler, resolver=resolver).invoke("c", "web_fetch", {"url": url})


# -- fetching ---------------------------------------------------------------


async def test_fetch_returns_text_and_taints():
    def handler(req):
        return httpx.Response(200, text="hello world", headers={"content-type": "text/plain"})

    result = await _fetch(handler)
    assert result.ok
    assert "hello world" in result.content
    # THE reason web_fetch exists behind taint: its content is untrusted from here.
    assert result.taint_source == "http://example.com/"


async def test_html_is_stripped_to_text():
    html = (
        "<html><head><title>Ttl</title><script>evil()</script>"
        "<style>x{color:red}</style></head><body><p>Hello <b>there</b></p></body></html>"
    )

    def handler(req):
        return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})

    result = await _fetch(handler)
    assert result.ok
    assert "Hello there" in result.content
    assert "evil()" not in result.content  # script dropped
    assert "color:red" not in result.content  # style dropped
    assert "<p>" not in result.content  # tags gone


async def test_http_error_status_is_a_result_not_a_failure():
    def handler(req):
        return httpx.Response(404, text="missing", headers={"content-type": "text/plain"})

    result = await _fetch(handler)
    assert result.ok
    assert "[HTTP 404]" in result.content
    assert "missing" in result.content


async def test_oversized_body_is_capped_at_the_tool_level():
    """MAX_FETCH_BYTES bounds what is read, before the registry's char cap — the
    memory guard, tested at the tool level where that later trim can't mask it."""
    big = b"x" * (MAX_FETCH_BYTES + 50_000)
    fn = web.build(
        client_factory=_client_factory(
            lambda req: httpx.Response(200, content=big, headers={"content-type": "text/plain"})
        ),
        resolver=_all_public(),
    )[0][0]
    out = await fn("http://example.com/")
    assert len(out.content.encode()) <= MAX_FETCH_BYTES + 100
    assert "truncated" in out.content


async def test_non_text_content_is_summarized_not_dumped():
    def handler(req):
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"}
        )

    result = await _fetch(handler)
    assert result.ok
    assert "image/png" in result.content
    assert "non-text" in result.content.lower()


# -- failures ---------------------------------------------------------------


async def test_timeout_is_reported():
    def handler(req):
        raise httpx.ConnectTimeout("slow")

    result = await _fetch(handler)
    assert (result.ok, result.code) == (False, "FETCH_TIMEOUT")


async def test_connection_error_is_reported():
    def handler(req):
        raise httpx.ConnectError("refused")

    result = await _fetch(handler)
    assert (result.ok, result.code) == (False, "FETCH_FAILED")


async def test_empty_url_is_refused():
    result = await _fetch(lambda req: httpx.Response(200, text="x"), url="   ")
    assert (result.ok, result.code) == (False, "URL_REQUIRED")


# -- redirects, re-validated every hop --------------------------------------


async def test_redirect_to_a_public_host_is_followed():
    def handler(req):
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://elsewhere.com/final"})
        return httpx.Response(200, text="landed", headers={"content-type": "text/plain"})

    result = await _fetch(handler)
    assert result.ok
    assert "landed" in result.content
    assert result.taint_source == "http://elsewhere.com/final"


async def test_redirect_to_a_blocked_host_is_refused():
    """The classic SSRF escalation: an allowed first hop 302s to the metadata
    endpoint. Every hop is re-validated, so this must be refused."""

    def handler(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    result = await _fetch(handler)
    assert (result.ok, result.code) == (False, "URL_BLOCKED")


async def test_a_redirect_loop_terminates():
    def handler(req):
        return httpx.Response(302, headers={"location": "http://example.com/again"})

    result = await _fetch(handler)
    assert (result.ok, result.code) == (False, "FETCH_FAILED")


# -- registration -----------------------------------------------------------


def test_web_fetch_is_registered_as_ask():
    r = _registry(lambda req: httpx.Response(200, text="x"))
    assert r.get("web_fetch").risk == ASK


def test_web_fetch_ships_with_and_without_a_sandbox(tmp_path):
    from jarvis_backend.security.permissions import SafeOnlyGate
    from jarvis_backend.security.sandbox import Sandbox
    from jarvis_backend.tools import default_registry

    assert default_registry(SafeOnlyGate()).get("web_fetch") is not None
    assert default_registry(SafeOnlyGate(), Sandbox([tmp_path])).get("web_fetch") is not None

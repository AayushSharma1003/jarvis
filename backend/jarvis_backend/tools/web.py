"""web_fetch: fetch a URL's text, after an SSRF check and a confirmation.

docs/security-model.md §3 and §4. This is the tool taint exists for — its result
is the canonical untrusted content, a web page the model will treat as
instructions unless the conversation is marked — and the tool that reaches the
network, so it is also where the SSRF guard (security/ssrf.py) is enforced.

Risk is `ask`: every fetch confirms, showing the URL, because a URL can carry
data *out* (exfiltration) and confirmation is the defense the SSRF guard cannot
provide. `safe` is off the table — web egress is a side effect, and §3's
invariant is that `safe` means read-only.

The care mirrors tools/shell.py, aimed at a socket instead of a subprocess:

- **Every hop is validated**, initial URL and each redirect target — a 302 to
  `http://169.254.169.254/` is how an allowed first hop becomes an internal one.
- **Bounded incremental read.** The body is read against a byte budget and the
  read stops at it; a huge response must not balloon RAM on the 8GB target.
- **A real timeout.** A slow server must not hold the single generation slot.
- **HTML is reduced to text** (stdlib), because a raw markup dump burns the
  model's context budget on `<script>`/`<style>`/tags.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from ..security import ssrf
from ..security.permissions import ASK
from .registry import Registry, ToolOutput

# The body is read against this budget and the read stops there — the memory/DoS
# guard, above the registry's MAX_RESULT_CHARS (8000) which does the final,
# model-facing trim (HANDOFF gotcha 15).
MAX_FETCH_BYTES = 512 * 1024

# A page that redirects more than this is either a loop or hostile.
MAX_REDIRECTS = 5

REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

# A slow server must not hold the single generation slot. Overridable for
# headless verification via JARVIS_FETCH_TIMEOUT_S — the packaged app never sets
# it, the same contract as the confirm broker's and shell's timeouts.
FETCH_TIMEOUT_S = 15.0


class WebError(Exception):
    """Raised with a machine-readable code; the frontend translates codes.

    Mirrors ssrf.SSRFError / sandbox.SandboxError / shell.ShellError. ssrf raises
    for URL / SSRF problems; this raises for fetch-level ones (empty argument,
    timeout, transport failure). Both carry `.code`, which the registry surfaces.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def fetch_timeout_s() -> float:
    raw = os.environ.get("JARVIS_FETCH_TIMEOUT_S")
    if not raw:
        return FETCH_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return FETCH_TIMEOUT_S
    return value if value > 0 else FETCH_TIMEOUT_S


class _TextExtractor(HTMLParser):
    """Collect visible text; drop the tags that are noise or code."""

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return re.sub(r"\s+", " ", " ".join(parser._parts)).strip()


def _is_texty(content_type: str) -> bool:
    ct = content_type.lower()
    return ct == "" or ct.startswith("text/") or "json" in ct or "xml" in ct or "html" in ct


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    lowered = content_type.lower()
    if "charset=" in lowered:
        charset = lowered.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _render(resp: httpx.Response, body: bytes, truncated: bool, final_url: str) -> ToolOutput:
    content_type = resp.headers.get("content-type", "")
    if not _is_texty(content_type):
        text = f"(non-text content: {content_type or 'unknown'}, {len(body)} bytes)"
        truncated = False  # a binary note is not a truncated page
    else:
        text = _decode(body, content_type)
        if "html" in content_type.lower():
            text = _html_to_text(text)
    if truncated:
        text += "\n… (output truncated)"
    if resp.status_code != 200:
        # The status is a result the model must see, like shell's exit code.
        tag = f"[HTTP {resp.status_code}]"
        text = f"{tag}\n{text}" if text else tag
    return ToolOutput(text, taint_source=final_url)


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,  # handled by hand, so every hop is re-validated
        headers={"User-Agent": "Jarvis/0.1 (local assistant)"},
    )


def build(*, client_factory=None, resolver=None) -> list[tuple]:
    """Return (fn, kwargs) registration specs. Injectable client + resolver so
    tests drive an httpx.MockTransport and a fake resolver, never real network."""
    make_client = client_factory or _default_client

    async def web_fetch(url: str) -> ToolOutput:
        """Fetch a web page and return its text."""
        if not url or not url.strip():
            raise WebError("URL_REQUIRED")
        current = url.strip()
        client = make_client()
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                # Validates the initial URL and every redirect target (§4).
                await ssrf.check_url(current, resolver=resolver)
                try:
                    async with client.stream(
                        "GET", current, timeout=fetch_timeout_s()
                    ) as resp:
                        if resp.status_code in REDIRECT_CODES:
                            location = resp.headers.get("location")
                            if not location:
                                raise WebError("FETCH_FAILED", "redirect without location")
                            current = urljoin(current, location)
                            continue
                        buffer = bytearray()
                        truncated = False
                        async for chunk in resp.aiter_bytes():
                            buffer.extend(chunk)
                            if len(buffer) >= MAX_FETCH_BYTES:
                                truncated = True
                                break
                        del buffer[MAX_FETCH_BYTES:]  # trim a large final chunk
                        return _render(resp, bytes(buffer), truncated, current)
                except httpx.TimeoutException as e:
                    raise WebError("FETCH_TIMEOUT", str(e)) from e
                except httpx.HTTPError as e:
                    raise WebError("FETCH_FAILED", str(e)) from e
            raise WebError("FETCH_FAILED", "too many redirects")
        finally:
            await client.aclose()

    return [
        (
            web_fetch,
            {
                "risk": ASK,
                "description": (
                    "Fetch a web page over http/https and return its text. Use the "
                    "full URL including the scheme. Confirms with the user first."
                ),
                "params": {"url": "The full URL to fetch, including http:// or https://"},
            },
        )
    ]


def register(registry: Registry, *, client_factory=None, resolver=None) -> None:
    for fn, kwargs in build(client_factory=client_factory, resolver=resolver):
        registry.register(fn, **kwargs)

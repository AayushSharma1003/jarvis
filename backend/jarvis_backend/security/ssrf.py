"""SSRF guard: resolve a URL and refuse the ones that reach inward.

docs/security-model.md §4 is normative. `web_fetch` is driven by an LLM that can
be talked into fetching anything by the content it reads, so "fetch this URL" is
an attacker-influenced action and the network egress is the threat, not an edge
case. This module is the check every URL — and every redirect hop — must pass.

Two layers:

1. **Scheme + parse.** Only `http`/`https`; `file://`, `gopher://`, `ftp://` and
   friends are pure SSRF vectors and are refused before anything is resolved. A
   URL we cannot parse into a scheme + host is refused too.
2. **Resolve + classify.** The host is resolved to its IPs and **every** one must
   be globally routable. If *any* resolves to a private / loopback / link-local /
   metadata / reserved address the whole URL is refused — a host with both a
   public and a private record is otherwise a trivial bypass. Classification is
   `ipaddress`-based, a superset of §4's explicit CIDR list: it covers IPv6, ULAs,
   IPv4-mapped addresses (`::ffff:127.0.0.1`), and alternate encodings that
   getaddrinfo decodes (decimal `2130706433` → `127.0.0.1`) without hand-rolling
   ranges.

**Known residual, documented not papered over** (mirrors §2's file-tool TOCTOU):
there is a window between the resolve we check here and the resolve httpx does at
connect time. An attacker who controls DNS for a host the model was steered to,
with a 0-TTL record, could return a public IP to us and a private one to the
socket (DNS rebinding). Closing it needs connecting to the exact validated IP
while preserving Host/SNI — fragile custom-transport plumbing deferred for v1.
The common vectors (direct internal IPs/hosts, the metadata endpoint, and a
redirect to an internal target) are all closed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# A resolver maps (host, port) -> list of IP strings. Injectable so tests never
# touch real DNS; the default wraps getaddrinfo.
Resolver = Callable[[str, int], Awaitable[list[str]]]


class SSRFError(Exception):
    """Raised with a machine-readable code; the frontend translates codes.

    Mirrors security/sandbox.py's SandboxError and tools/shell.py's ShellError:
    the registry reads `.code` and turns it into a failed ToolResult, so a refused
    URL is a result the model can react to, never a crash.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def is_blocked_ip(ip: str) -> bool:
    """True if `web_fetch` must not connect to this address.

    Pure and exhaustively tested. Fails closed: an address we cannot parse is
    blocked. IPv4-mapped IPv6 is unwrapped so the v4 rules apply to it.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    # Unwrap IPv4-mapped IPv6 (::ffff:10.0.0.1) so the v4 rules apply. Current
    # CPython classifies these natively, so a test can't distinguish this line —
    # it is kept as cross-version defense: the native handling was not reliable
    # across early 3.11.x, and the project targets >=3.11.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


async def _default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def check_url(url: str, *, resolver: Resolver | None = None) -> None:
    """Refuse `url` (raising SSRFError) unless it is safe to fetch.

    Called for the initial URL and again for every redirect target, because a
    302 to `http://169.254.169.254/` is the classic way an allowed first hop
    becomes an internal one.
    """
    resolve = resolver or _default_resolver
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme and scheme not in ALLOWED_SCHEMES:
        raise SSRFError("URL_SCHEME_BLOCKED", scheme)
    if scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        # Empty/garbage, or an http(s) URL with no host to resolve.
        raise SSRFError("URL_INVALID", url)

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        # An IP literal is its own address — validate it directly and never
        # resolve it. Resolving `http://169.254.169.254/` would hand the literal
        # to DNS, where a test's fake resolver or an attacker's records could
        # whitewash it.
        ipaddress.ip_address(parsed.hostname)
        ips = [parsed.hostname]
    except ValueError:
        try:
            ips = await resolve(parsed.hostname, port)
        except OSError as e:  # socket.gaierror is an OSError subclass
            raise SSRFError("FETCH_FAILED", f"dns: {e}") from e
        if not ips:
            raise SSRFError("FETCH_FAILED", "dns: no addresses") from None

    for ip in ips:
        if is_blocked_ip(ip):
            # First blocked address wins: one internal record poisons the host.
            raise SSRFError("URL_BLOCKED", ip)

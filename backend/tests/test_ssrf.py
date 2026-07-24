"""SSRF guard: which URLs web_fetch may reach, and which it must refuse.

docs/security-model.md §4. Two layers, both here:
  - scheme + parse: only http/https, and a URL we can't parse is refused.
  - resolve + classify: every resolved IP must be globally routable; ANY
    private / loopback / link-local / metadata address refuses the whole URL.

The classifier (`is_blocked_ip`) is pure and tested exhaustively; `check_url`
takes an injectable resolver so these tests never touch real DNS — except one
`localhost` case that exercises the real getaddrinfo path (resolvable from
/etc/hosts, so still no network) and doubles as the loopback-block proof.
"""

from __future__ import annotations

import socket

import pytest

from jarvis_backend.security.ssrf import SSRFError, check_url, is_blocked_ip

BLOCKED = [
    "127.0.0.1",
    "127.0.0.53",  # loopback /8, not just .1
    "0.0.0.0",  # unspecified
    "10.0.0.5",
    "172.16.9.9",
    "192.168.1.1",  # private
    "169.254.169.254",  # link-local + the cloud metadata endpoint
    "::1",  # IPv6 loopback
    "fc00::1",
    "fd12:3456::1",  # IPv6 ULA (fc00::/7)
    "fe80::1",  # IPv6 link-local
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",  # IPv4-mapped IPv6
    "notanip",  # unparseable ⇒ fail closed
]

ALLOWED = [
    "93.184.216.34",
    "8.8.8.8",
    "1.1.1.1",
    "2606:2800:220:1:248:1893:25c8:1946",  # a public IPv6
]


@pytest.mark.parametrize("ip", BLOCKED)
def test_blocked_ips(ip):
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ALLOWED)
def test_globally_routable_ips_are_allowed(ip):
    assert is_blocked_ip(ip) is False


def _resolver(mapping):
    async def resolve(host, port):
        try:
            return mapping[host]
        except KeyError:
            raise socket.gaierror(f"no such host: {host}") from None

    return resolve


async def test_public_host_passes():
    await check_url(
        "http://example.com/page", resolver=_resolver({"example.com": ["93.184.216.34"]})
    )


async def test_private_host_is_blocked():
    with pytest.raises(SSRFError) as e:
        await check_url("http://intranet/", resolver=_resolver({"intranet": ["10.0.0.5"]}))
    assert e.value.code == "URL_BLOCKED"


async def test_any_blocked_ip_refuses_the_whole_host():
    """A host with one public and one private record must be refused — the any-IP
    rule; otherwise it is a trivial bypass."""
    resolver = _resolver({"mixed": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(SSRFError) as e:
        await check_url("http://mixed/", resolver=resolver)
    assert e.value.code == "URL_BLOCKED"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://host/"])
async def test_non_http_schemes_are_blocked(url):
    with pytest.raises(SSRFError) as e:
        await check_url(url, resolver=_resolver({}))
    assert e.value.code == "URL_SCHEME_BLOCKED"


@pytest.mark.parametrize("url", ["", "not a url", "http://", "example.com"])
async def test_unparseable_urls_are_refused(url):
    with pytest.raises(SSRFError) as e:
        await check_url(url, resolver=_resolver({}))
    assert e.value.code == "URL_INVALID"


async def test_dns_failure_is_a_fetch_failure():
    with pytest.raises(SSRFError) as e:
        await check_url("http://nx.invalid/", resolver=_resolver({}))
    assert e.value.code == "FETCH_FAILED"


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/"])
async def test_ip_literal_hosts_are_validated_without_resolving(url):
    """An IP literal is its own address; it must be blocked directly, never handed
    to the resolver — which a test, or an attacker's DNS, could whitewash."""
    called = []

    async def resolver(host, port):
        called.append(host)
        return ["93.184.216.34"]  # would whitewash the literal if consulted

    with pytest.raises(SSRFError) as e:
        await check_url(url, resolver=resolver)
    assert e.value.code == "URL_BLOCKED"
    assert called == [], "an IP literal must not be resolved"


async def test_public_ip_literal_passes():
    await check_url("http://93.184.216.34/", resolver=_resolver({}))


async def test_localhost_is_blocked_through_the_real_resolver():
    """Exercises the default getaddrinfo path (the injected-resolver tests bypass
    it) and proves the loopback block fires end to end. localhost is in
    /etc/hosts, so no network — and this is the guard against fetching the local
    Ollama on 11434."""
    with pytest.raises(SSRFError) as e:
        await check_url("http://localhost:11434/")
    assert e.value.code == "URL_BLOCKED"

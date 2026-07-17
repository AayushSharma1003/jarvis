"""WebSocket auth: per-session token + strict Origin check.

Security model §4: the backend binds 127.0.0.1 only, but other local processes
and drive-by browser pages can still reach localhost. The token (passed to the
Tauri shell out-of-band, never in a URL) blocks the former; the Origin check
blocks the latter. A missing Origin header is allowed — non-browser clients
(tests, CLI) don't send one and can't be coerced by a web page.
"""

from __future__ import annotations

import hmac
import secrets

ALLOWED_ORIGINS = frozenset(
    {
        "tauri://localhost",       # macOS / Linux webview
        "http://tauri.localhost",  # Windows webview
        "https://tauri.localhost",
        "http://localhost:1420",   # vite dev server
        "http://127.0.0.1:1420",
    }
)


def make_token() -> str:
    return secrets.token_urlsafe(32)


def token_valid(expected: str, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())


def origin_allowed(origin: str | None) -> bool:
    return origin is None or origin in ALLOWED_ORIGINS

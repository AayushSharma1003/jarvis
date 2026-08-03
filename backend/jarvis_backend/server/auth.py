"""WebSocket auth: per-session token + strict Origin check.

Security model §4: the backend binds 127.0.0.1 only, but other local processes
and drive-by browser pages can still reach localhost.

**The Origin check is the one that carries weight.** A browser always sends
Origin on a WebSocket handshake and a page cannot forge it, so a drive-by
against localhost is refused. A missing Origin is allowed on purpose —
non-browser clients (tests, CLI) don't send one, and a web page cannot omit it.

**The token is a handshake, not an authorisation boundary between local
processes, and this docstring used to claim otherwise.** It reaches the sidecar
in its environment, which any process running as the same user can read (`ps
eww`, `/proc/<pid>/environ`). What it genuinely buys: unrelated software cannot
stumble into the port, and another *user* on the machine cannot drive Jarvis.
What it does not buy: protection from code already running as you — which can
also answer any confirmation, since those are broadcast and first-answer-wins
(security/confirm.py). That is inside the trust boundary, because such a process
can already do everything Jarvis can. Written down in §4 rather than implied.
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


def token_valid(expected: str, provided: object) -> bool:
    # `provided` is whatever came out of the client's JSON, so it is typed
    # `object`: a non-string must be a refusal, not an AttributeError out of the
    # pre-auth path (nothing catches it there — see server/app.py's handshake).
    if not isinstance(provided, str) or not provided:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())


def origin_allowed(origin: str | None) -> bool:
    return origin is None or origin in ALLOWED_ORIGINS

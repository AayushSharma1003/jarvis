"""Drive the REAL packaged app's voice path over WebSocket, with no screen.

MANUAL script — needs a running Jarvis (packaged or dev), a working speaker and
a working microphone, so it is not part of the pytest suite. Same rules as
probe_tool_calling.py: don't rename it to `test_...` unless you teach CI to skip
it. Nothing here writes to the repo; `mic` and `acoustic` create a conversation
in whatever data dir the running app is using, so point the app at a scratch
JARVIS_DATA_DIR if that matters.

    cd backend
    uv run python tests/manual/probe_voice_live.py control   # ALWAYS run first
    uv run python tests/manual/probe_voice_live.py mic
    uv run python tests/manual/probe_voice_live.py acoustic

Connection details are discovered from the running sidecar (its token is in its
environment, its port from lsof), so there is nothing to paste. Override with
`--port` / `--token` when driving a dev server.

WHY THIS EXISTS. Three separate voice outages shipped green:

  * gotcha 33 — the assistant said its own wake word and interrupted itself.
    Invisible because every acoustic test in the project's history used a reply
    that happened not to contain "Jarvis". `acoustic` therefore ASSERTS the
    reply contained it, and reports the run invalid if it did not — a pass on a
    reply that could not have triggered the bug is not a pass.
  * gotcha 35/36 — the microphone was revoked, then denied outright by the
    hardened runtime. `mic` reads `voice.level`, which is the RMS of each mic
    chunk, and reports the one number that distinguishes a dead microphone from
    a quiet room: whether ANY sample was non-zero. Measured during the outage:
    0/79 non-zero. After the fix: 58/59, max 0.506.
  * the near-miss that nearly published a false pass — an acoustic run that
    "passed" with the wake service switched OFF. `[wake] enabled` in config.toml
    is NOT the toggle; wake state lives in state.toml in the data dir. So
    `control` exists and everything else is meaningless without it: it proves
    the detector fires on an idle app before any negative result is trusted.

Run `control` first. Always.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys

try:
    import websockets
except ImportError:  # pragma: no cover - manual script
    sys.exit("pip/uv install websockets first")

WAKE_AIFF = "/tmp/jarvis_probe_wake.aiff"


# ---------------------------------------------------------------- discovery


def discover() -> tuple[str, str]:
    """Find the running sidecar's port and token. Non-browser clients may omit
    the Origin header, which auth.py allows, so no header juggling is needed."""
    pids = subprocess.run(
        ["pgrep", "-f", "jarvis-backend/jarvis-backend"], capture_output=True, text=True
    ).stdout.split()
    if not pids:
        sys.exit("no running jarvis-backend found — start the app first")
    pid = pids[0]
    env = subprocess.run(["ps", "eww", pid], capture_output=True, text=True).stdout
    token = next(
        (w.split("=", 1)[1] for w in env.split() if w.startswith("JARVIS_WS_TOKEN=")), ""
    )
    lsof = subprocess.run(
        ["lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    port = ""
    for line in lsof[1:]:
        if "127.0.0.1:" in line:
            port = line.rsplit("127.0.0.1:", 1)[1].split()[0].strip("( )")
            break
    if not (port and token):
        have = "set" if token else "unset"
        sys.exit(f"could not discover connection (port={port!r} token={have})")
    return port, token


def say(text: str, loud: bool = False) -> None:
    """Speak into the room. `loud` is for barge-in: shouting over the app's own
    playback needs the volume boost (there is no AEC, which is the point)."""
    if loud:
        subprocess.run(["say", "-r", "160", "-o", WAKE_AIFF, text], check=False)
        subprocess.run(["afplay", "-v", "2", WAKE_AIFF], check=False)
    else:
        subprocess.run(["say", "-r", "175", text], check=False)


async def connect(port: str, token: str):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/ws")
    await ws.send(json.dumps({"type": "auth", "token": token}))
    greeting = [json.loads(await ws.recv()) for _ in range(2)]
    return ws, greeting


# ------------------------------------------------------------------- probes


async def probe_control(port: str, token: str) -> bool:
    """Prove the detector fires on an idle app. Everything else depends on it."""
    ws, greeting = await connect(port, token)
    async with ws:
        status = next((m for m in greeting if m.get("type") == "wake.status"), {})
        print(f"wake.status: {json.dumps(status)}")
        if not status.get("available"):
            print("FAIL: wake models unavailable")
            return False
        if not status.get("enabled"):
            print("wake disabled — enabling via wake.set (config.toml is NOT the toggle)")
            await ws.send(json.dumps({"type": "wake.set", "enabled": True}))
            if not json.loads(await ws.recv()).get("enabled"):
                print("FAIL: could not enable wake")
                return False

        await asyncio.sleep(1.0)
        print(">> saying the wake word into an idle app")
        say("Hey Jarvis")
        deadline = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            except TimeoutError:
                continue
            if msg.get("type") == "wake.detected":
                print("CONTROL PASSED: detector fired on an idle app")
                return True
        print("CONTROL FAILED: no wake.detected in 20s — negative results below mean nothing")
        return False


async def probe_mic(port: str, token: str) -> bool:
    """Dead microphone vs quiet room. Exact zeros are the discriminator."""
    ws, _ = await connect(port, token)
    async with ws:
        await ws.send(json.dumps({"type": "voice.start"}))
        levels: list[float] = []
        spoke = False
        deadline = asyncio.get_event_loop().time() + 60
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            except TimeoutError:
                continue
            t = msg.get("type")
            if t == "voice.level":
                levels.append(msg["level"])
            elif t == "voice.state":
                print(f"   state: {msg.get('state')} {msg.get('reason', '')}")
                if msg.get("state") == "listening" and not spoke:
                    spoke = True
                    await asyncio.sleep(0.4)
                    say("What is the capital of France?")
                elif msg.get("state") == "idle":
                    break

        nonzero = [x for x in levels if x != 0.0]
        print(f"\nvoice.level: {len(nonzero)}/{len(levels)} non-zero, max={max(levels, default=0)}")
        if not levels:
            # No data is not good news. The commonest cause is the mic never
            # opening at all: Pa_OpenStream blocks while macOS waits for an
            # unanswered permission prompt, so the turn parks in "loading".
            print("VERDICT: INCONCLUSIVE — no audio frames at all; the turn never reached")
            print("  'listening'. The microphone open is probably blocked waiting for a")
            print("  permission decision. Focus the app and press Cmd-M, then approve.")
            return False
        if not nonzero:
            app = "/Applications/Jarvis.app"
            print("VERDICT: MIC IS DELIVERING DIGITAL SILENCE (denied, or muted in hardware)")
            print(f"  check: codesign -dvvv {app}  -> flags must include 'runtime'")
            print(f"         codesign -d --entitlements - {app}  -> needs audio-input")
            return False
        print("VERDICT: MIC IS LIVE")
        return True


async def _turn(ws, utterance: str, barge_in: bool = False) -> tuple[str, str | None]:
    await ws.send(json.dumps({"type": "voice.start"}))
    reply: list[str] = []
    reason, fired, spoke = None, False, False
    deadline = asyncio.get_event_loop().time() + 120
    while asyncio.get_event_loop().time() < deadline:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        except TimeoutError:
            continue
        t = msg.get("type")
        if t == "chat.delta":
            reply.append(msg.get("text", ""))
        elif t == "voice.state":
            st = msg.get("state")
            print(f"   state: {st} {msg.get('reason', '')}")
            if st == "listening" and not spoke:
                spoke = True
                await asyncio.sleep(0.4)
                say(utterance)
            elif st == "speaking" and barge_in and not fired:
                fired = True
                await asyncio.sleep(1.6)
                print("   >> BARGE-IN: 'Hey Jarvis' over the reply")
                say("Hey Jarvis", loud=True)
            elif st == "idle":
                reason = msg.get("reason")
                break
    return "".join(reply), reason


async def probe_acoustic(port: str, token: str) -> bool:
    """The two M6.2 checks: self-wake suppression, and barge-in still alive."""
    ws, _ = await connect(port, token)
    async with ws:
        print("=== A. 'Introduce yourself' must speak its whole reply ===")
        reply, reason = await _turn(ws, "Introduce yourself")
        hazard = "jarvis" in reply.casefold()
        print(f"   reply: {reply.strip()[:200]!r}")
        print(f"   reason={reason!r} reply_contains_wake_word={hazard}")
        if not hazard:
            print("   A: INVALID — the reply never said the wake word, so the")
            print("      hazard was absent and this run proves nothing (gotcha 33).")
            a_ok = False
        else:
            a_ok = reason == "done"
            print(f"   A: {'PASS' if a_ok else 'FAIL'}")

        await asyncio.sleep(3)

        print("\n=== B. barge-in over an ordinary reply must cut it off ===")
        reply, reason = await _turn(ws, "Tell me a long story about the ocean", barge_in=True)
        # B's validity condition is the mirror of A's. If this reply happens to
        # say "Jarvis", the exchange SUPPRESSES wake for the rest of the turn by
        # design (gotcha 33's fix), so barge-in cannot fire and a FAIL here would
        # be the fix working, not a regression. Same trap as A, opposite sign.
        b_hazard = "jarvis" in reply.casefold()
        spoken_s = len(reply.split()) / 2.6  # ~156 wpm; the barge-in fires at 1.6s
        print(f"   reply: {reply.strip()[:160]!r}")
        print(f"   words={len(reply.split())} (~{spoken_s:.1f}s of speech)")
        if b_hazard:
            print("   B: INVALID — this reply contains the wake word, so wake was")
            print("      deliberately suppressed for the turn. Re-run for another reply.")
            b_ok = False
        elif spoken_s < 3.0:
            print("   B: INVALID — reply too short; playback likely ended before the")
            print("      barge-in was spoken. Re-run for a longer reply.")
            b_ok = False
        else:
            b_ok = reason == "stopped"
            print(f"   reason={reason!r}")
            print(f"   B: {'PASS' if b_ok else 'FAIL'}")

        print(f"\nA self-wake suppression: {'PASS' if a_ok else 'FAIL'}")
        print(f"B barge-in still live:   {'PASS' if b_ok else 'FAIL'}")
        return a_ok and b_ok


PROBES = {"control": probe_control, "mic": probe_mic, "acoustic": probe_acoustic}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("probe", choices=sorted(PROBES))
    ap.add_argument("--port")
    ap.add_argument("--token")
    args = ap.parse_args()

    port, token = (args.port, args.token) if args.port and args.token else discover()
    print(f"connecting to 127.0.0.1:{port}\n")
    return 0 if asyncio.run(PROBES[args.probe](port, token)) else 1


if __name__ == "__main__":
    raise SystemExit(main())

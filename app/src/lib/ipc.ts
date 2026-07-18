// Bridge to the Tauri shell. The Rust side spawns the Python sidecar, parses
// its "ready" line, and exposes {port, token} via the backend_info command,
// emitting "backend-ready" when it arrives and "backend-exited" if it dies.
//
// Hard-won ordering rules (see the phase-1 handshake postmortem):
// 1. Register event listeners BEFORE querying state, or an event fired in
//    between is lost forever.
// 2. Never rely on events alone — backend_info is ALSO polled every second,
//    so a broken event layer degrades to a 1s-latency handshake instead of a
//    silent timeout.
// 3. Never swallow a listener registration failure; log it loudly.

import { debugLog } from "./debug";
import type { BackendInfo } from "./types";

const HANDSHAKE_TIMEOUT_MS = 30_000;
const POLL_INTERVAL_MS = 1_000;

function inTauri(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

/** Resolves when the sidecar is up. Outside Tauri (plain `vite dev`), falls
 * back to VITE_JARVIS_PORT/VITE_JARVIS_TOKEN pointing at a manually started
 * backend. */
export async function getBackendInfo(): Promise<BackendInfo> {
  if (!inTauri()) {
    const port = Number(import.meta.env.VITE_JARVIS_PORT ?? 0);
    const token = String(import.meta.env.VITE_JARVIS_TOKEN ?? "");
    if (!port || !token) {
      debugLog("ipc: not in Tauri and no VITE_JARVIS_* env -> BACKEND_UNAVAILABLE");
      throw new Error("BACKEND_UNAVAILABLE");
    }
    debugLog(`ipc: dev fallback, port ${port}`);
    return { port, token };
  }
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");

  return new Promise<BackendInfo>((resolve, reject) => {
    let settled = false;
    const cleanups: (() => void)[] = [];
    const settle = (outcome: () => void) => {
      if (settled) return;
      settled = true;
      for (const c of cleanups) c();
      outcome();
    };

    const timeout = setTimeout(() => {
      debugLog("ipc: handshake timeout -> BACKEND_TIMEOUT");
      settle(() => reject(new Error("BACKEND_TIMEOUT")));
    }, HANDSHAKE_TIMEOUT_MS);
    cleanups.push(() => clearTimeout(timeout));

    // Listeners first (rule 1). Registration failure is logged but NOT fatal:
    // polling below still completes the handshake (rule 2).
    listen<BackendInfo>("backend-ready", (event) => {
      debugLog(`ipc: backend-ready event, port ${event.payload.port}`);
      settle(() => resolve(event.payload));
    }).then(
      (unlisten) => {
        debugLog("ipc: listen(backend-ready) registered");
        cleanups.push(unlisten);
      },
      (e) => debugLog(`ipc: listen(backend-ready) REJECTED: ${String(e)} — relying on polling`),
    );
    listen("backend-exited", () => {
      debugLog("ipc: backend-exited event -> BACKEND_EXITED");
      settle(() => reject(new Error("BACKEND_EXITED")));
    }).then(
      (unlisten) => cleanups.push(unlisten),
      (e) => debugLog(`ipc: listen(backend-exited) REJECTED: ${String(e)}`),
    );

    // Poll immediately, then every second (rule 2).
    const poll = () => {
      invoke<BackendInfo | null>("backend_info").then(
        (info) => {
          if (info) {
            debugLog(`ipc: backend_info poll -> port ${info.port}`);
            settle(() => resolve(info));
          }
        },
        (e) => debugLog(`ipc: backend_info invoke failed: ${String(e)}`),
      );
    };
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    cleanups.push(() => clearInterval(interval));
  });
}

export function onBackendExited(handler: () => void): void {
  if (!inTauri()) return;
  void import("@tauri-apps/api/event").then(({ listen }) =>
    listen("backend-exited", handler).then(
      () => debugLog("ipc: onBackendExited listener registered"),
      (e) => debugLog(`ipc: onBackendExited listen REJECTED: ${String(e)}`),
    ),
  );
}

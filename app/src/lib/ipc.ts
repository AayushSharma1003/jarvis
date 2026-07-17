// Bridge to the Tauri shell. The Rust side spawns the Python sidecar, parses
// its "ready" line, and exposes {port, token} via the backend_info command,
// emitting "backend-ready" when it arrives and "backend-exited" if it dies.

import type { BackendInfo } from "./types";

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
      throw new Error("BACKEND_UNAVAILABLE");
    }
    return { port, token };
  }
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");

  const existing = await invoke<BackendInfo | null>("backend_info");
  if (existing) return existing;

  return new Promise<BackendInfo>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("BACKEND_TIMEOUT")), 30_000);
    void listen<BackendInfo>("backend-ready", (event) => {
      clearTimeout(timeout);
      resolve(event.payload);
    });
    void listen("backend-exited", () => {
      clearTimeout(timeout);
      reject(new Error("BACKEND_EXITED"));
    });
  });
}

export function onBackendExited(handler: () => void): void {
  if (!inTauri()) return;
  void import("@tauri-apps/api/event").then(({ listen }) =>
    listen("backend-exited", handler),
  );
}

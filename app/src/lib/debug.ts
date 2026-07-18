// Frontend diagnostics. Everything goes to console.debug; inside Tauri it is
// ALSO routed to the Rust process's stderr via the frontend_log command, so
// handshake failures show up in `tauri dev` output and user bug reports —
// the webview console is invisible exactly when you need it most.

let toRust: ((message: string) => void) | null = null;

if ("__TAURI_INTERNALS__" in window) {
  void import("@tauri-apps/api/core")
    .then(({ invoke }) => {
      toRust = (message) => {
        invoke("frontend_log", { message }).catch(() => {
          // If even logging is blocked, the console copy still exists.
        });
      };
      toRust("frontend logging online");
    })
    .catch(() => {});
}

export function debugLog(...parts: unknown[]): void {
  const message = parts
    .map((p) => (typeof p === "string" ? p : JSON.stringify(p)))
    .join(" ");
  console.debug("[jarvis]", message);
  toRust?.(message);
}

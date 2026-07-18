// WebSocket client: auth handshake, JSON messages, bounded-backoff reconnect.

import { debugLog } from "./debug";
import type { ClientMessage, ServerMessage } from "./types";

export type SocketStatus = "connecting" | "ready" | "closed";

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8_000;

export class JarvisSocket {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private closedByUs = false;

  constructor(
    private port: number,
    private token: string,
    private onMessage: (msg: ServerMessage) => void,
    private onStatus: (status: SocketStatus) => void,
  ) {}

  connect(): void {
    this.closedByUs = false;
    this.onStatus("connecting");
    debugLog(`ws: connecting to ws://127.0.0.1:${this.port}/ws`);
    // Token goes in the first message, never in the URL (see auth.py).
    const ws = new WebSocket(`ws://127.0.0.1:${this.port}/ws`);
    this.ws = ws;

    ws.onopen = () => {
      debugLog("ws: open, sending auth");
      this.sendRaw({ type: "auth", token: this.token });
    };
    ws.onmessage = (event) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(event.data as string) as ServerMessage;
      } catch {
        debugLog("ws: unparseable message dropped");
        return;
      }
      if (msg.type === "ready") {
        debugLog(`ws: ready (backend v${msg.version})`);
        this.attempts = 0;
        this.onStatus("ready");
      } else if (msg.type === "error") {
        debugLog(`ws: server error code=${msg.code}`);
      }
      this.onMessage(msg);
    };
    ws.onclose = (event) => {
      debugLog(
        `ws: closed code=${event.code} reason=${event.reason || "(none)"} clean=${event.wasClean}`,
      );
      this.onStatus("closed");
      if (!this.closedByUs) this.scheduleReconnect();
    };
  }

  send(msg: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      debugLog(`ws: send(${msg.type}) dropped, socket not open`);
      return false;
    }
    this.sendRaw(msg);
    return true;
  }

  close(): void {
    this.closedByUs = true;
    this.ws?.close();
  }

  private sendRaw(msg: ClientMessage): void {
    this.ws?.send(JSON.stringify(msg));
  }

  private scheduleReconnect(): void {
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.attempts, RECONNECT_MAX_MS);
    this.attempts += 1;
    debugLog(`ws: reconnect #${this.attempts} in ${delay}ms`);
    setTimeout(() => {
      if (!this.closedByUs) this.connect();
    }, delay);
  }
}

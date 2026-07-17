// WebSocket client: auth handshake, JSON messages, bounded-backoff reconnect.

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
    // Token goes in the first message, never in the URL (see auth.py).
    const ws = new WebSocket(`ws://127.0.0.1:${this.port}/ws`);
    this.ws = ws;

    ws.onopen = () => {
      this.sendRaw({ type: "auth", token: this.token });
    };
    ws.onmessage = (event) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(event.data as string) as ServerMessage;
      } catch {
        return;
      }
      if (msg.type === "ready") {
        this.attempts = 0;
        this.onStatus("ready");
      }
      this.onMessage(msg);
    };
    ws.onclose = () => {
      this.onStatus("closed");
      if (!this.closedByUs) this.scheduleReconnect();
    };
  }

  send(msg: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
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
    setTimeout(() => {
      if (!this.closedByUs) this.connect();
    }, delay);
  }
}

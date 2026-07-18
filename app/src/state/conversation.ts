// Session state: connection, models, and the active conversation's messages.
// Mirrors the backend's active path only — branch navigation arrives in phase 5.

import { create } from "zustand";
import { getBackendInfo, onBackendExited } from "../lib/ipc";
import { JarvisSocket, type SocketStatus } from "../lib/ws";
import type { ModelEntry, ServerMessage } from "../lib/types";

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export type AppStatus = "starting" | SocketStatus | "backend-lost";

interface ConversationState {
  status: AppStatus;
  errorCode: string | null;
  models: ModelEntry[];
  currentModel: string;
  conversationId: string | null;
  messages: UiMessage[];
  streamingText: string | null; // non-null while an assistant reply streams
  init: () => Promise<void>;
  send: (text: string) => void;
  stop: () => void;
  setModel: (model: string) => void;
}

let socket: JarvisSocket | null = null;
// Synchronous guard: React StrictMode double-mounts effects, and `socket` is
// only assigned after an await — two concurrent init() calls would open two
// WebSockets and double-apply every streamed delta (seen in the wild).
let initStarted = false;

export const useConversation = create<ConversationState>((set, get) => ({
  status: "starting",
  errorCode: null,
  models: [],
  currentModel: "",
  conversationId: null,
  messages: [],
  streamingText: null,

  init: async () => {
    if (initStarted) return;
    initStarted = true;
    onBackendExited(() => set({ status: "backend-lost" }));
    try {
      const info = await getBackendInfo();
      socket = new JarvisSocket(
        info.port,
        info.token,
        (msg) => handleMessage(msg, set, get),
        (status) => {
          set({ status });
          if (status === "ready") socket?.send({ type: "models.list" });
        },
      );
      socket.connect();
    } catch (e) {
      set({ status: "backend-lost", errorCode: (e as Error).message });
    }
  },

  send: (text: string) => {
    const { conversationId, currentModel, streamingText } = get();
    if (streamingText !== null || !socket) return;
    const ok = socket.send({
      type: "chat.send",
      content: text,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(currentModel ? { model: currentModel } : {}),
    });
    if (ok) {
      set((s) => ({
        errorCode: null,
        streamingText: "",
        messages: [
          ...s.messages,
          { id: crypto.randomUUID(), role: "user", content: text },
        ],
      }));
    }
  },

  stop: () => {
    socket?.send({ type: "chat.stop" });
  },

  setModel: (model: string) => set({ currentModel: model }),
}));

function handleMessage(
  msg: ServerMessage,
  set: (fn: Partial<ConversationState> | ((s: ConversationState) => Partial<ConversationState>)) => void,
  get: () => ConversationState,
): void {
  switch (msg.type) {
    case "models":
      set({
        models: msg.models,
        currentModel: get().currentModel || msg.default,
      });
      break;
    case "chat.start":
      set({ conversationId: msg.conversation_id });
      break;
    case "chat.delta":
      set((s) => ({ streamingText: (s.streamingText ?? "") + msg.text }));
      break;
    case "chat.done":
      set((s) => ({
        streamingText: null,
        messages:
          s.streamingText !== null
            ? [
                ...s.messages,
                {
                  id: msg.turn_id,
                  role: "assistant",
                  content: s.streamingText + (msg.interrupted ? " …" : ""),
                },
              ]
            : s.messages,
      }));
      break;
    case "error":
      set((s) => ({
        errorCode: msg.code,
        // A terminal error ends any in-flight stream; keep partial text.
        streamingText: null,
        messages:
          s.streamingText
            ? [
                ...s.messages,
                { id: crypto.randomUUID(), role: "assistant", content: s.streamingText },
              ]
            : s.messages,
      }));
      break;
    default:
      break;
  }
}

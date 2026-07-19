// Session state: connection, models, and the active conversation's messages.
// Mirrors the backend's active path only — branch navigation arrives in phase 5.

import { create } from "zustand";
import { getBackendInfo, onBackendExited } from "../lib/ipc";
import { JarvisSocket, type SocketStatus } from "../lib/ws";
import type { ModelEntry, ServerMessage, VoiceState } from "../lib/types";

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
  voiceState: VoiceState;
  voiceLevel: number; // smoothed 0–1 audio level (mic or TTS) for the sphere
  voiceHint: string | null; // e.g. "no_speech" — transient, not an error
  wakeEnabled: boolean; // the persistent "Hey Jarvis" toggle (backend-owned)
  wakeAvailable: boolean; // wake models fetched & runtime present
  init: () => Promise<void>;
  send: (text: string) => void;
  stop: () => void;
  toggleVoice: () => void;
  setWakeEnabled: (enabled: boolean) => void;
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
  voiceState: "idle",
  voiceLevel: 0,
  voiceHint: null,
  wakeEnabled: false,
  wakeAvailable: false,

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

  toggleVoice: () => {
    const { voiceState, conversationId, currentModel, streamingText } = get();
    if (voiceState !== "idle") {
      socket?.send({ type: "voice.stop" });
      return;
    }
    if (streamingText !== null) return; // a text reply is still streaming
    const ok = socket?.send({
      type: "voice.start",
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(currentModel ? { model: currentModel } : {}),
    });
    if (ok) set({ errorCode: null, voiceHint: null });
  },

  setWakeEnabled: (enabled: boolean) => {
    // Optimistic-free: the backend confirms via wake.status (it's the one
    // persisting the toggle), so the UI only flips when it really happened.
    socket?.send({ type: "wake.set", enabled });
  },

  setModel: (model: string) => set({ currentModel: model }),
}));

// Dev console handle (vite dev only): lets you drive voiceState/voiceLevel by
// hand to exercise the sphere without a live mic turn.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__jarvisStore = useConversation;
}

/** The wake word was heard (backend already cancelled any active reply). */
function startVoiceFromWake(get: () => ConversationState): void {
  const { voiceState, streamingText, conversationId, currentModel } = get();
  if (voiceState !== "idle" || streamingText !== null || !socket) return;
  socket.send({
    type: "voice.start",
    ...(conversationId ? { conversation_id: conversationId } : {}),
    ...(currentModel ? { model: currentModel } : {}),
  });
}

function handleMessage(
  msg: ServerMessage,
  set: (fn: Partial<ConversationState> | ((s: ConversationState) => Partial<ConversationState>)) => void,
  get: () => ConversationState,
): void {
  switch (msg.type) {
    case "voice.state":
      set({
        voiceState: msg.state,
        ...(msg.state === "idle"
          ? {
              voiceLevel: 0,
              voiceHint: msg.reason === "no_speech" ? "no_speech" : null,
            }
          : {}),
      });
      break;
    case "stt.text":
      // The transcribed utterance becomes a normal user message; the reply
      // then arrives via chat.start/delta/done exactly like a typed turn.
      set((s) => ({
        streamingText: "",
        messages: [
          ...s.messages,
          { id: crypto.randomUUID(), role: "user", content: msg.text },
        ],
      }));
      break;
    case "voice.level":
      set({ voiceLevel: msg.level });
      break;
    case "wake.status":
      set({ wakeEnabled: msg.enabled, wakeAvailable: msg.available });
      break;
    case "wake.detected":
      startVoiceFromWake(get);
      break;
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

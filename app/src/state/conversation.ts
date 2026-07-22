// Session state: connection, models, the conversation list, and the messages of
// every conversation the user has opened this session.
//
// Why a map of threads rather than one flat message array: a reply keeps
// generating after you switch away, and it must land in the conversation it was
// asked in (Claude/ChatGPT behaviour). `threads` is keyed by conversation id —
// plus NEW_THREAD for the unsaved chat, which has no id until the backend
// answers chat.start. `messages`/`streamingText` are a mirror of the *active*
// thread so components (and the sphere) keep reading them directly.
//
// Only ONE generation runs at a time: the backend allows one per connection and
// answers BUSY otherwise, so `streamKey` is a single value, not a set.
//
// Branch navigation (siblings/tree) is still phase 5; this mirrors the active
// path only.

import { create } from "zustand";
import { getBackendInfo, onBackendExited } from "../lib/ipc";
import { JarvisSocket, type SocketStatus } from "../lib/ws";
import type {
  ConversationSummary,
  HistoryTurn,
  ModelEntry,
  RamTier,
  ReadinessCheck,
  ServerMessage,
  VoiceState,
} from "../lib/types";

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export type AppStatus = "starting" | SocketStatus | "backend-lost";

/** Key for the unsaved chat: it has no conversation id until chat.start. */
const NEW_THREAD = "__new__";

interface Thread {
  messages: UiMessage[];
  streamingText: string | null; // non-null while an assistant reply streams
}

const EMPTY_THREAD: Thread = { messages: [], streamingText: null };

/** Errors only conversation.rename / conversation.delete can raise. The client
 *  validates their other failure mode (empty title / missing id) before
 *  sending, so this is the whole set. */
const MANAGEMENT_ERROR_CODES = new Set(["CONVERSATION_NOT_FOUND"]);

export interface ConversationState {
  status: AppStatus;
  errorCode: string | null;
  models: ModelEntry[];
  currentModel: string;
  modelSource: "configured" | "auto";
  tier: RamTier | null; // what this machine can run; null until models arrive
  readiness: ReadinessCheck[] | null; // null = not checked yet
  ready: boolean; // false only once a check has actually failed
  conversations: ConversationSummary[];
  conversationId: string | null; // the conversation on screen; null = new chat
  threads: Record<string, Thread>;
  streamKey: string | null; // thread owning the in-flight generation
  messages: UiMessage[]; // mirror of the active thread
  streamingText: string | null; // mirror of the active thread
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
  recheckReadiness: () => void;
  newChat: () => void;
  switchTo: (conversationId: string) => void;
  rename: (conversationId: string, title: string) => void;
  remove: (conversationId: string) => void;
}

let socket: JarvisSocket | null = null;
// Synchronous guard: React StrictMode double-mounts effects, and `socket` is
// only assigned after an await — two concurrent init() calls would open two
// WebSockets and double-apply every streamed delta (seen in the wild).
let initStarted = false;

type SetState = (
  fn: Partial<ConversationState> | ((s: ConversationState) => Partial<ConversationState>),
) => void;

const keyOf = (conversationId: string | null): string => conversationId ?? NEW_THREAD;

/** Update one thread, keeping the active-thread mirror in step. */
function patchThread(
  set: SetState,
  key: string,
  fn: (t: Thread) => Thread,
): void {
  set((s) => {
    const next = fn(s.threads[key] ?? EMPTY_THREAD);
    const threads = { ...s.threads, [key]: next };
    return key === keyOf(s.conversationId)
      ? { threads, messages: next.messages, streamingText: next.streamingText }
      : { threads };
  });
}

/** Point the view at a thread, mirroring its contents. */
function showThread(set: SetState, conversationId: string | null): void {
  set((s) => {
    const t = s.threads[keyOf(conversationId)] ?? EMPTY_THREAD;
    return {
      conversationId,
      messages: t.messages,
      streamingText: t.streamingText,
      errorCode: null,
      voiceHint: null,
    };
  });
}

/** Flatten a history path into chat bubbles. Tool messages are dropped —
 *  UiMessage is user|assistant, and surfacing tool spans is phase 4's job. */
function messagesFromHistory(turns: HistoryTurn[]): UiMessage[] {
  return turns.flatMap((turn) =>
    turn.messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ id: m.id, role: m.role as "user" | "assistant", content: m.content })),
  );
}

export const useConversation = create<ConversationState>((set, get) => ({
  status: "starting",
  errorCode: null,
  models: [],
  currentModel: "",
  modelSource: "auto",
  tier: null,
  readiness: null,
  ready: true, // optimistic: never flash the gate before we've asked
  conversations: [],
  conversationId: null,
  threads: {},
  streamKey: null,
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
          if (status === "ready") {
            // A fresh connection doesn't inherit the old one's failure — an
            // "Ollama unreachable" banner must not outlive starting Ollama.
            set({ errorCode: null });
            socket?.send({ type: "models.list" });
            socket?.send({ type: "conversations.list" });
            socket?.send({ type: "system.readiness" });
          }
        },
      );
      socket.connect();
    } catch (e) {
      set({ status: "backend-lost", errorCode: (e as Error).message });
    }
  },

  send: (text: string) => {
    const { conversationId, currentModel, streamKey } = get();
    if (streamKey !== null || !socket) return; // one generation at a time
    const key = keyOf(conversationId);
    const ok = socket.send({
      type: "chat.send",
      content: text,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(currentModel ? { model: currentModel } : {}),
    });
    if (!ok) return;
    set({ errorCode: null, streamKey: key });
    patchThread(set, key, (t) => ({
      streamingText: "",
      messages: [...t.messages, { id: crypto.randomUUID(), role: "user", content: text }],
    }));
  },

  stop: () => {
    socket?.send({ type: "chat.stop" });
  },

  toggleVoice: () => {
    const { voiceState, conversationId, currentModel, streamKey } = get();
    if (voiceState !== "idle") {
      socket?.send({ type: "voice.stop" });
      return;
    }
    if (streamKey !== null) return; // a reply is still generating somewhere
    const ok = socket?.send({
      type: "voice.start",
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(currentModel ? { model: currentModel } : {}),
    });
    // Claim the generation slot now: the spoken turn belongs to the
    // conversation that was open when the mic opened, even if the user
    // navigates away while Jarvis is listening.
    if (ok) set({ errorCode: null, voiceHint: null, streamKey: keyOf(conversationId) });
  },

  setWakeEnabled: (enabled: boolean) => {
    // Optimistic-free: the backend confirms via wake.status (it's the one
    // persisting the toggle), so the UI only flips when it really happened.
    socket?.send({ type: "wake.set", enabled });
  },

  setModel: (model: string) => set({ currentModel: model }),

  // Re-runs the gate after the user has gone and fixed something (started
  // Ollama, pulled a model, fetched the voice assets) without restarting.
  recheckReadiness: () => {
    socket?.send({ type: "system.readiness" });
    socket?.send({ type: "models.list" });
  },

  newChat: () => {
    // Don't wipe the unsaved thread if it's mid-generation — its chat.start is
    // still coming, and that reply belongs to the conversation being created.
    if (get().streamKey !== NEW_THREAD) {
      set((s) => ({ threads: { ...s.threads, [NEW_THREAD]: EMPTY_THREAD } }));
    }
    showThread(set, null);
  },

  switchTo: (conversationId: string) => {
    const { conversationId: current, streamKey } = get();
    if (conversationId === current) return;
    showThread(set, conversationId);
    // Re-read from the store unless a reply is streaming into this very
    // conversation — that partial text isn't persisted yet, so the cached
    // thread is the truthful one until chat.done.
    if (streamKey !== conversationId) {
      socket?.send({ type: "conversation.history", conversation_id: conversationId });
    }
  },

  rename: (conversationId: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    socket?.send({ type: "conversation.rename", conversation_id: conversationId, title: trimmed });
  },

  remove: (conversationId: string) => {
    socket?.send({ type: "conversation.delete", conversation_id: conversationId });
    set((s) => {
      const threads = { ...s.threads };
      delete threads[conversationId];
      return {
        threads,
        // The backend cancels the generation it was writing into.
        streamKey: s.streamKey === conversationId ? null : s.streamKey,
      };
    });
    // Watching the conversation that just went away → fall back to a new chat.
    if (get().conversationId === conversationId) showThread(set, null);
  },
}));

/** True when a reply is generating in a conversation the user isn't looking at.
 *  The backend has one generation slot per connection, so the composer has to
 *  wait rather than send into a BUSY refusal. */
export function isBusyElsewhere(s: ConversationState): boolean {
  return s.streamKey !== null && s.streamKey !== keyOf(s.conversationId);
}

// Dev console handle (vite dev only): lets you drive voiceState/voiceLevel by
// hand to exercise the sphere without a live mic turn.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__jarvisStore = useConversation;
}

/** The wake word was heard (backend already cancelled any active reply). */
function startVoiceFromWake(set: SetState, get: () => ConversationState): void {
  const { voiceState, streamKey, conversationId, currentModel } = get();
  if (voiceState !== "idle" || streamKey !== null || !socket) return;
  const ok = socket.send({
    type: "voice.start",
    ...(conversationId ? { conversation_id: conversationId } : {}),
    ...(currentModel ? { model: currentModel } : {}),
  });
  if (ok) set({ streamKey: keyOf(conversationId) });
}

function handleMessage(msg: ServerMessage, set: SetState, get: () => ConversationState): void {
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
      // A voice turn that ended without ever reaching the LLM (no speech, an
      // error, an early stop) still holds the generation slot it claimed.
      if (msg.state === "idle") {
        const { streamKey, threads } = get();
        if (streamKey !== null && (threads[streamKey]?.streamingText ?? null) === null) {
          set({ streamKey: null });
        }
      }
      break;
    case "stt.text": {
      // The transcribed utterance becomes a normal user message; the reply
      // then arrives via chat.start/delta/done exactly like a typed turn.
      const key = get().streamKey ?? keyOf(get().conversationId);
      patchThread(set, key, (t) => ({
        streamingText: "",
        messages: [...t.messages, { id: crypto.randomUUID(), role: "user", content: msg.text }],
      }));
      break;
    }
    case "voice.level":
      set({ voiceLevel: msg.level });
      break;
    case "wake.status":
      set({ wakeEnabled: msg.enabled, wakeAvailable: msg.available });
      break;
    case "wake.detected":
      startVoiceFromWake(set, get);
      break;
    case "models":
      set({
        models: msg.models,
        tier: msg.tier,
        modelSource: msg.source,
        currentModel: get().currentModel || msg.default,
      });
      break;
    case "readiness":
      set({ readiness: msg.checks, ready: msg.ready });
      break;
    case "chat.start": {
      const { streamKey, conversationId } = get();
      if (streamKey === NEW_THREAD) {
        // The unsaved chat just became real: move its thread under the id the
        // backend assigned, and follow it if the user is still looking at it.
        set((s) => {
          const threads = { ...s.threads };
          threads[msg.conversation_id] = threads[NEW_THREAD] ?? EMPTY_THREAD;
          delete threads[NEW_THREAD];
          return { threads, streamKey: msg.conversation_id };
        });
        if (conversationId === null) showThread(set, msg.conversation_id);
      } else {
        set({ streamKey: msg.conversation_id });
      }
      socket?.send({ type: "conversations.list" }); // a new conversation may have appeared
      break;
    }
    case "chat.delta": {
      const key = get().streamKey;
      if (key === null) break;
      patchThread(set, key, (t) => ({
        ...t,
        streamingText: (t.streamingText ?? "") + msg.text,
      }));
      break;
    }
    case "chat.done": {
      const key = get().streamKey;
      if (key !== null) {
        patchThread(set, key, (t) =>
          t.streamingText !== null
            ? {
                streamingText: null,
                messages: [
                  ...t.messages,
                  {
                    id: msg.turn_id,
                    role: "assistant",
                    content: t.streamingText + (msg.interrupted ? " …" : ""),
                  },
                ],
              }
            : { ...t, streamingText: null },
        );
      }
      set({ streamKey: null });
      socket?.send({ type: "conversations.list" }); // titles + updated_at ordering
      break;
    }
    case "conversations": {
      set({ conversations: msg.conversations });
      // Deleted from another window? Don't strand the user on a dead thread.
      const { conversationId } = get();
      if (conversationId && !msg.conversations.some((c) => c.id === conversationId)) {
        showThread(set, null);
      }
      break;
    }
    case "history":
      // Never clobber a conversation that's mid-reply: the streaming turn isn't
      // in the store yet, so history would erase visible text.
      if (get().streamKey !== msg.conversation_id) {
        patchThread(set, msg.conversation_id, () => ({
          messages: messagesFromHistory(msg.turns),
          streamingText: null,
        }));
      }
      break;
    case "error": {
      const key = get().streamKey;
      set({ errorCode: msg.code });
      // A terminal error ends any in-flight stream; keep the partial text.
      // Management failures are the exception: errors carry no correlation id,
      // and a rename racing a deleted conversation says nothing about the reply
      // being generated somewhere else — tearing it down would lose real text.
      if (key !== null && !MANAGEMENT_ERROR_CODES.has(msg.code)) {
        patchThread(set, key, (t) =>
          t.streamingText
            ? {
                streamingText: null,
                messages: [
                  ...t.messages,
                  { id: crypto.randomUUID(), role: "assistant", content: t.streamingText },
                ],
              }
            : { ...t, streamingText: null },
        );
        set({ streamKey: null });
      }
      break;
    }
    default:
      break;
  }
}

// Mirrors backend/jarvis_backend/server/protocol.py. Keep the two in sync.

export interface BackendInfo {
  port: number;
  token: string;
}

/** May this model be handed a tool schema?
 *  "on"          curated in catalog/models.toml and measured
 *  "optin"       capable template, unvetted — off unless the user enables it
 *  "unsupported" the chat template has no tool support at all
 *  Backend reasoning: backend/jarvis_backend/llm/capabilities.py */
export type ToolSupport = "on" | "optin" | "unsupported";

export interface ModelEntry {
  id: string;
  parameter_size: string | null;
  size_bytes: number | null;
  params_b: number | null; // parsed parameter count, billions
  over_budget: boolean; // too big for this machine's RAM tier
  tools: ToolSupport;
}

/** What this machine can comfortably run — drives the picker's "why". */
export interface RamTier {
  ram_gb: number;
  budget_b: number;
}

/** One row of the first-run gate. `code` is absent when status is "ok". */
export interface ReadinessCheck {
  id: "llm" | "model" | "tools" | "voice_models" | "wake_models" | "microphone";
  status: "ok" | "warn" | "fail";
  code?: string;
  data?: Record<string, unknown>;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

/** One tool call and its outcome. `code` is machine-readable; the wording
 *  lives in i18n/en.json under `tool.code.*`. */
export interface ToolSpanData {
  name: string;
  arguments: Record<string, unknown>;
  content: string;
  ok: boolean;
  code: string;
}

/** How the user may answer a confirmation. "session" is only offered for `ask`
 *  tools — the backend refuses to remember a `dangerous` one regardless. */
export type ConfirmAnswer = "deny" | "once" | "session";

/** One pending permission request. `id` is a correlation id the BACKEND minted;
 *  an answer only counts against an id it is still waiting on, so there is no
 *  message this client can send that approves something out of nowhere.
 *  Backend: backend/jarvis_backend/security/confirm.py */
export interface ConfirmRequest {
  id: string;
  name: string;
  risk: "safe" | "ask" | "dangerous";
  arguments: Record<string, unknown>;
  conversation_id: string;
  voice: boolean;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
}

export interface HistoryTurn {
  id: string;
  parent_turn_id: string | null;
  messages: HistoryMessage[];
}

export type VoiceState =
  | "idle"
  | "loading"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking";

export type ServerMessage =
  | { type: "ready"; version: string }
  | { type: "pong" }
  | { type: "voice.state"; state: VoiceState; reason?: string }
  | { type: "stt.text"; text: string }
  | { type: "voice.level"; level: number }
  | { type: "chat.start"; conversation_id: string; model: string }
  | { type: "chat.delta"; text: string }
  | ({ type: "tool.span"; call_id: string } & ToolSpanData)
  | {
      type: "chat.done";
      conversation_id: string;
      turn_id: string;
      interrupted: boolean;
    }
  | {
      type: "models";
      default: string;
      source: "configured" | "auto";
      tier: RamTier;
      models: ModelEntry[];
    }
  | { type: "readiness"; ready: boolean; checks: ReadinessCheck[] }
  | { type: "conversations"; conversations: ConversationSummary[] }
  | { type: "history"; conversation_id: string; turns: HistoryTurn[] }
  | { type: "wake.status"; enabled: boolean; available: boolean }
  | { type: "wake.detected" }
  | ({ type: "confirm.request" } & ConfirmRequest)
  | { type: "confirm.close"; id: string; reason: string }
  | { type: "error"; code: string; detail?: string };

export type ClientMessage =
  | { type: "auth"; token: string }
  | { type: "ping" }
  | {
      type: "chat.send";
      content: string;
      conversation_id?: string;
      model?: string;
      parent_turn_id?: string;
    }
  | { type: "chat.stop" }
  | { type: "voice.start"; conversation_id?: string; model?: string }
  | { type: "voice.stop" }
  | { type: "models.list" }
  | { type: "system.readiness" }
  | { type: "conversations.list" }
  | { type: "conversation.history"; conversation_id: string }
  | { type: "conversation.rename"; conversation_id: string; title: string }
  | { type: "conversation.delete"; conversation_id: string }
  | { type: "wake.set"; enabled: boolean }
  | { type: "confirm.respond"; id: string; answer: ConfirmAnswer }
  | { type: "voice.say"; text: string };

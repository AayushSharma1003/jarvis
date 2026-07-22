// Mirrors backend/jarvis_backend/server/protocol.py. Keep the two in sync.

export interface BackendInfo {
  port: number;
  token: string;
}

export interface ModelEntry {
  id: string;
  parameter_size: string | null;
  size_bytes: number | null;
  params_b: number | null; // parsed parameter count, billions
  over_budget: boolean; // too big for this machine's RAM tier
}

/** What this machine can comfortably run — drives the picker's "why". */
export interface RamTier {
  ram_gb: number;
  budget_b: number;
}

/** One row of the first-run gate. `code` is absent when status is "ok". */
export interface ReadinessCheck {
  id: "llm" | "model" | "voice_models" | "wake_models" | "microphone";
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
  | { type: "wake.set"; enabled: boolean };

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
  id: "config" | "llm" | "model" | "tools" | "voice_models" | "wake_models" | "microphone";
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
  /** Where untrusted content in this conversation came from — a file path
   *  today, a URL once web_fetch exists. Empty when the conversation is clean.
   *  Non-empty also means the call is not grantable, so the dialog hides
   *  "allow for this session" (the backend refuses to record one regardless).
   *  Backend: security/taint.py, docs/security-model.md §3. */
  reason: string;
}

/** One tool an extension declares, at the risk the CORE will register it —
 *  which may be higher than the manifest asked for (network = true floors every
 *  tool to `ask`). Never show the declared level; this is the enforced one. */
export interface ExtensionTool {
  name: string;
  risk: "safe" | "ask" | "dangerous";
}

/** One installed extension, as the approval panel sees it. `status` and `code`
 *  are machine-readable (wording in i18n `extension.status.*` / `extension.code.*`).
 *  Everything else is what §5 says the user must see BEFORE approving.
 *  Backend: server/protocol.py `extensions`, extensions/loader.py. */
export interface ExtensionInfo {
  name: string;
  status: "approved" | "pending" | "changed" | "unsupported_platform" | "invalid";
  code: string; // why it is invalid; "" otherwise
  /** SHA-256 of its exact bytes on disk. Echoed back on approve so the backend
   *  can refuse (`EXTENSION_CHANGED`) if the folder changed since it was shown —
   *  approving what you never read is the failure §5 exists to prevent. */
  digest: string;
  /** approved is consent; loaded is whether the code actually ran. They diverge
   *  when an approved extension fails to import. */
  loaded: boolean;
  version: string;
  description: string;
  platforms: string[];
  os_permissions: string[];
  network: boolean;
  tools: ExtensionTool[];
}

// Something happened that the user should be told about, with no request in
// flight — an extension's timer firing (M5.4). Unlike every other server
// message this one is unsolicited, which is why it carries its own id.
//
// `code` + `data` rather than a sentence: the backend emits machine-readable
// codes and this side owns every word (the i18n rule). `data` is display
// payload the user supplied — a timer's label — already truncated and
// JSON-safe by the time it arrives (extensions/host.py sanitizes it).
export interface JarvisNotification {
  id: string;
  source: string; // the extension that sent it; untrusted, display only
  code: string;
  data: Record<string, string | number | boolean | null>;
  speak: boolean;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
}

export interface HistoryTurn {
  id: string;
  parent_turn_id: string | null;
  /** Every alternative for this turn, oldest first, INCLUDING this one (M5.5).
   *  The whole set rather than a count, so the arrows know where to point: a
   *  count says a branch exists, an array says where it is. Always at least
   *  `[id]`, so the "show a switcher" rule is a plain `length > 1`. */
  siblings: string[];
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
  | { type: "assets.progress"; name: string; done: number; total: number }
  | { type: "assets.done"; ok: boolean; failed: string[] }
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
  | { type: "extensions"; extensions: ExtensionInfo[] }
  | ({ type: "notification" } & JarvisNotification)
  | { type: "error"; code: string; detail?: string };

export type ClientMessage =
  | { type: "auth"; token: string }
  | { type: "ping" }
  | {
      type: "chat.send";
      content: string;
      conversation_id?: string;
      model?: string;
      /** Which turn to fork from (M5.5). **Omit** to carry on from the live
       *  branch; send `null` to fork at the root, i.e. edit the very first
       *  message. The backend keeps absent and null apart deliberately —
       *  see protocol.parent_turn_from. */
      parent_turn_id?: string | null;
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
  | { type: "conversation.branch"; conversation_id: string; turn_id: string }
  | { type: "wake.set"; enabled: boolean }
  | { type: "confirm.respond"; id: string; answer: ConfirmAnswer }
  // `notification_id` makes the backend's speech single-use: the notification
  // went to every open window and each one answers, so without it Jarvis says
  // the same line once per window. Absent for the confirm prompt, which may
  // legitimately repeat.
  | { type: "voice.say"; text: string; notification_id?: string }
  | { type: "extensions.list" }
  | { type: "extensions.approve"; name: string; digest: string }
  | { type: "extensions.revoke"; name: string }
  | { type: "assets.fetch" };

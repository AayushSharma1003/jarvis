import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { errorText } from "../../i18n";
import { isBusyElsewhere, useConversation } from "../../state/conversation";
import { Readiness } from "../onboarding/Readiness";
import { visualStateOf } from "../sphere/params";
import { Composer } from "./Composer";
import { ConfirmDialog } from "./ConfirmDialog";
import { ConversationList } from "./ConversationList";
import { MessageList } from "./MessageList";

// three.js is ~550 kB minified — split it out so the chat shell paints first
// and the orb fades in a beat later.
const SphereOrb = lazy(() =>
  import("../sphere/SphereOrb").then((m) => ({ default: m.SphereOrb })),
);

const STATUS_DOT: Record<string, string> = {
  ready: "bg-emerald-500",
  connecting: "bg-amber-500",
  starting: "bg-amber-500",
  closed: "bg-amber-500",
  "backend-lost": "bg-red-500",
};

const SIDEBAR_KEY = "jarvis.sidebar.open";
const NARROW_QUERY = "(max-width: 640px)";

function MenuIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      className="h-4 w-4"
      aria-hidden
    >
      <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
    </svg>
  );
}

/** Below this width the sidebar overlays the chat instead of pushing it —
 *  a 640px window has no room to give 256px away permanently. */
function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(NARROW_QUERY);
    const onChange = () => setNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

export function ChatView() {
  const { t } = useTranslation();
  const s = useConversation();
  const narrow = useNarrow();
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) !== "0",
  );

  const toggleSidebar = () => {
    setSidebarOpen((open) => {
      localStorage.setItem(SIDEBAR_KEY, open ? "0" : "1");
      return !open;
    });
  };

  // Push-to-talk hotkey: ⌘M / Ctrl+M toggles the voice loop.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "m") {
        e.preventDefault();
        useConversation.getState().toggleVoice();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The orb takes center stage during voice states and on an empty chat;
  // the spacer below the header keeps messages flowing under it (same
  // 500 ms ease as the orb's own move in SphereOrb).
  const orbCentered =
    visualStateOf(s.voiceState) !== "idle" ||
    (s.messages.length === 0 && s.streamingText === null);

  // A reply generating in a conversation you're not looking at still occupies
  // the backend's single generation slot — say so rather than letting the send
  // bounce off a BUSY error.
  const busyElsewhere = isBusyElsewhere(s);

  // Nothing can be asked until the backend reports a usable setup. Warnings
  // (no voice models, no mic) don't gate anything: typing still works.
  const blocked = s.readiness !== null && !s.ready;

  // "Why this model" — the RAM tier the backend picked against.
  const tierNote = s.tier
    ? t(s.modelSource === "configured" ? "model.whyConfigured" : "model.whyAuto", {
        ram: s.tier.ram_gb,
        budget: s.tier.budget_b,
      })
    : undefined;

  const modelLabel = (m: (typeof s.models)[number]) => {
    if (m.params_b === null) return m.id;
    const key = m.over_budget ? "model.optionOverBudget" : "model.option";
    return t(key, { id: m.id, params: m.params_b, ram: s.tier?.ram_gb ?? "?" });
  };

  // Why tools are off for this model. An `optin` model is capable on paper but
  // nobody has measured whether it can DECLINE a tool, which M4.0 established
  // is a security property, not a quality one — see llm/capabilities.py.
  const current = s.models.find((m) => m.id === s.currentModel);
  const toolNote =
    current && current.tools !== "on" ? t(`model.tools.${current.tools}`) : undefined;

  const afterSelect = () => {
    if (narrow) setSidebarOpen(false);
  };

  return (
    <div className="relative flex h-full bg-zinc-900 text-zinc-100">
      {sidebarOpen && narrow && (
        <div
          onClick={() => setSidebarOpen(false)}
          className="absolute inset-0 z-20 bg-black/60"
          aria-hidden
        />
      )}
      {sidebarOpen && (
        <aside className={narrow ? "absolute inset-y-0 left-0 z-30 shadow-2xl" : "shrink-0"}>
          <ConversationList
            conversations={s.conversations}
            activeId={s.conversationId}
            streamingId={s.streamKey}
            onSelect={(id) => {
              s.switchTo(id);
              afterSelect();
            }}
            onNew={() => {
              s.newChat();
              afterSelect();
            }}
            onRename={s.rename}
            onDelete={s.remove}
          />
        </aside>
      )}

      <div className="relative flex h-full min-w-0 flex-1 flex-col">
        <header
          data-tauri-drag-region
          className="flex items-center gap-3 border-b border-zinc-800 px-4 py-2.5"
        >
          <button
            onClick={toggleSidebar}
            aria-expanded={sidebarOpen}
            aria-label={sidebarOpen ? t("conversation.hideList") : t("conversation.toggleList")}
            title={sidebarOpen ? t("conversation.hideList") : t("conversation.toggleList")}
            className="-ml-1 rounded-lg p-1.5 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
          >
            <MenuIcon />
          </button>
          <span className={`h-2 w-2 rounded-full ${STATUS_DOT[s.status]}`} />
          <span className="text-sm font-medium">{t("app.title")}</span>
          <span className="text-xs text-zinc-500">
            {s.voiceState !== "idle" ? t(`voice.${s.voiceState}`) : t(`status.${s.status}`)}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {s.wakeAvailable && (
              <button
                onClick={() => s.setWakeEnabled(!s.wakeEnabled)}
                aria-pressed={s.wakeEnabled}
                title={s.wakeEnabled ? t("wake.disable") : t("wake.enable")}
                className={`flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition-colors ${
                  s.wakeEnabled
                    ? "bg-sky-950 text-sky-300 hover:bg-sky-900"
                    : "bg-zinc-800 text-zinc-500 hover:bg-zinc-700"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    s.wakeEnabled ? "animate-pulse bg-sky-400" : "bg-zinc-600"
                  }`}
                />
                {t("wake.label")}
              </button>
            )}
            <select
              value={s.currentModel}
              onChange={(e) => s.setModel(e.target.value)}
              disabled={s.models.length === 0}
              aria-label={t("model.label")}
              title={[tierNote, toolNote].filter(Boolean).join("\n\n")}
              className="max-w-56 rounded-lg bg-zinc-800 px-2 py-1 text-xs text-zinc-300 outline-none"
            >
              {s.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {modelLabel(m)}
                </option>
              ))}
            </select>
          </div>
        </header>

        <Suspense fallback={null}>
          <SphereOrb />
        </Suspense>
        <div
          aria-hidden="true"
          className="shrink-0 transition-[height] duration-500 ease-out"
          style={{ height: orbCentered ? 300 : 0 }}
        />

        {blocked ? (
          <Readiness checks={s.readiness ?? []} onRecheck={s.recheckReadiness} />
        ) : (
          <MessageList
            messages={s.messages}
            streamingText={s.streamingText}
            subtitle={[tierNote, toolNote].filter(Boolean).join(" ")}
          />
        )}

        {/* While the gate is up it already explains the problem in full; the
            error banner would just say "can't reach Ollama" a second time. */}
        {s.errorCode && !blocked && (
          <div className="mx-4 mb-2 rounded-lg bg-red-950/60 px-3 py-2 text-xs text-red-300">
            {errorText(s.errorCode)}
          </div>
        )}
        {s.voiceHint && !s.errorCode && (
          <div className="mx-4 mb-2 rounded-lg bg-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
            {t(`voice.hint.${s.voiceHint}`)}
          </div>
        )}
        {busyElsewhere && !s.errorCode && (
          <div className="mx-4 mb-2 rounded-lg bg-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
            {t("conversation.busyElsewhere")}
          </div>
        )}

        <Composer
          disabled={s.status !== "ready" || busyElsewhere || blocked}
          streaming={s.streamingText !== null}
          voiceState={s.voiceState}
          onSend={s.send}
          onStop={s.stop}
          onVoiceToggle={s.toggleVoice}
        />
      </div>

      {/* Outermost so it covers the sidebar too — a permission dialog you can
          click around is not a permission dialog. One at a time: the rest of
          the queue surfaces as each is answered. */}
      {s.pendingConfirms.length > 0 && (
        <ConfirmDialog request={s.pendingConfirms[0]} onAnswer={s.respondConfirm} />
      )}
    </div>
  );
}

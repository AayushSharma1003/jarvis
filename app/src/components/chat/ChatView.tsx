import { lazy, Suspense, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { errorText } from "../../i18n";
import { useConversation } from "../../state/conversation";
import { visualStateOf } from "../sphere/params";
import { Composer } from "./Composer";
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

export function ChatView() {
  const { t } = useTranslation();
  const s = useConversation();

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

  return (
    <div className="relative flex h-full flex-col bg-zinc-900 text-zinc-100">
      <header
        data-tauri-drag-region
        className="flex items-center gap-3 border-b border-zinc-800 px-4 py-2.5"
      >
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
            className="max-w-44 rounded-lg bg-zinc-800 px-2 py-1 text-xs text-zinc-300 outline-none"
          >
            {s.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
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

      <MessageList messages={s.messages} streamingText={s.streamingText} />

      {s.errorCode && (
        <div className="mx-4 mb-2 rounded-lg bg-red-950/60 px-3 py-2 text-xs text-red-300">
          {errorText(s.errorCode)}
        </div>
      )}
      {s.voiceHint && !s.errorCode && (
        <div className="mx-4 mb-2 rounded-lg bg-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
          {t(`voice.hint.${s.voiceHint}`)}
        </div>
      )}

      <Composer
        disabled={s.status !== "ready"}
        streaming={s.streamingText !== null}
        voiceState={s.voiceState}
        onSend={s.send}
        onStop={s.stop}
        onVoiceToggle={s.toggleVoice}
      />
    </div>
  );
}

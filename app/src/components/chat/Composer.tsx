import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { VoiceState } from "../../lib/types";

interface Props {
  disabled: boolean;
  streaming: boolean;
  voiceState: VoiceState;
  onSend: (text: string) => void;
  onStop: () => void;
  onVoiceToggle: () => void;
}

const VOICE_ACTIVE_STYLE: Record<string, string> = {
  loading: "bg-zinc-700 text-zinc-300",
  listening: "bg-sky-600 text-white animate-pulse",
  transcribing: "bg-amber-600 text-white",
  thinking: "bg-amber-600 text-white animate-pulse",
  speaking: "bg-violet-600 text-white animate-pulse",
};

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4" aria-hidden>
      <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z" />
      <path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.93V20H8a1 1 0 1 0 0 2h8a1 1 0 1 0 0-2h-3v-2.07A7 7 0 0 0 19 11Z" />
    </svg>
  );
}

export function Composer({ disabled, streaming, voiceState, onSend, onStop, onVoiceToggle }: Props) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const voiceActive = voiceState !== "idle";

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || streaming) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <div className="flex items-end gap-2 border-t border-zinc-800 p-3">
      <button
        onClick={onVoiceToggle}
        disabled={disabled || (streaming && !voiceActive)}
        aria-label={voiceActive ? t("voice.stop") : t("voice.start")}
        title={`${voiceActive ? t("voice.stop") : t("voice.start")} (⌘M)`}
        className={`rounded-xl px-3 py-2.5 disabled:opacity-40 ${
          VOICE_ACTIVE_STYLE[voiceState] ?? "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
        }`}
      >
        <MicIcon />
      </button>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={t("chat.placeholder")}
        rows={1}
        className="max-h-40 flex-1 resize-none rounded-xl bg-zinc-800 px-3.5 py-2.5 text-sm text-zinc-100 outline-none placeholder:text-zinc-500 focus:ring-1 focus:ring-sky-600"
        style={{ userSelect: "text", WebkitUserSelect: "text", cursor: "text" }}
      />
      {streaming ? (
        <button
          onClick={onStop}
          className="rounded-xl bg-zinc-700 px-4 py-2.5 text-sm font-medium text-zinc-100 hover:bg-zinc-600"
        >
          {t("chat.stop")}
        </button>
      ) : (
        <button
          onClick={submit}
          disabled={disabled || !text.trim()}
          className="rounded-xl bg-sky-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40 hover:bg-sky-500"
        >
          {t("chat.send")}
        </button>
      )}
    </div>
  );
}

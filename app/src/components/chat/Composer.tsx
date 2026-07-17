import { useState } from "react";
import { useTranslation } from "react-i18next";

interface Props {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function Composer({ disabled, streaming, onSend, onStop }: Props) {
  const { t } = useTranslation();
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || streaming) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <div className="flex items-end gap-2 border-t border-zinc-800 p-3">
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

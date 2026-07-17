import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { UiMessage } from "../../state/conversation";

interface Props {
  messages: UiMessage[];
  streamingText: string | null;
}

function Bubble({ role, content }: { role: UiMessage["role"]; content: string }) {
  const user = role === "user";
  return (
    <div className={`flex ${user ? "justify-end" : "justify-start"}`}>
      <div
        data-selectable
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          user ? "bg-sky-600 text-white" : "bg-zinc-800 text-zinc-100"
        }`}
      >
        {content}
      </div>
    </div>
  );
}

export function MessageList({ messages, streamingText }: Props) {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [messages, streamingText]);

  if (messages.length === 0 && streamingText === null) {
    return (
      <div className="flex flex-1 items-center justify-center px-8 text-center text-sm text-zinc-500">
        {t("chat.empty")}
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
      {messages.map((m) => (
        <Bubble key={m.id} role={m.role} content={m.content} />
      ))}
      {streamingText !== null && (
        <Bubble role="assistant" content={streamingText || "…"} />
      )}
      <div ref={bottomRef} />
    </div>
  );
}

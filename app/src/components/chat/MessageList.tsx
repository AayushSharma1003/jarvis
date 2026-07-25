import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TurnMeta, UiMessage } from "../../state/conversation";
import { BranchSwitcher } from "./BranchSwitcher";
import { ToolSpan } from "./ToolSpan";

interface Props {
  messages: UiMessage[];
  streamingText: string | null;
  /** Path metadata for the branch on screen — what makes forking possible. */
  turns?: TurnMeta[];
  /** Fork: `parentTurnId` is a turn id, or `null` to fork at the root. */
  onFork?: (text: string, parentTurnId: string | null) => void;
  onBranch?: (turnId: string) => void;
  /** Editing and regenerating both start a generation, so they are hidden
   *  while one is already running — the backend answers BUSY otherwise. */
  busy?: boolean;
  /** Shown under the empty-chat line — currently "why this model". */
  subtitle?: string;
}

function Bubble({
  role,
  content,
  children,
}: {
  role: UiMessage["role"];
  content: string;
  children?: React.ReactNode;
}) {
  const user = role === "user";
  if (!content) return null;
  return (
    <div className={`group flex flex-col ${user ? "items-end" : "items-start"}`}>
      <div
        data-selectable
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          user ? "bg-sky-600 text-white" : "bg-zinc-800 text-zinc-100"
        }`}
      >
        {content}
      </div>
      {children}
    </div>
  );
}

/** The edit box a user bubble turns into. Enter saves, Escape cancels —
 *  Shift+Enter still writes a newline, matching the composer. */
function EditBox({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.setSelectionRange(initial.length, initial.length);
  }, [initial.length]);

  const save = () => {
    const trimmed = text.trim();
    if (trimmed) onSave(trimmed);
  };

  return (
    <div className="w-full max-w-[85%]">
      <textarea
        ref={ref}
        value={text}
        rows={Math.min(8, text.split("\n").length + 1)}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          } else if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            save();
          }
        }}
        className="w-full resize-none rounded-2xl bg-zinc-800 px-4 py-2.5 text-sm leading-relaxed text-zinc-100 outline-none ring-1 ring-sky-600"
      />
      <div className="mt-1.5 flex justify-end gap-2 text-xs">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-2.5 py-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
        >
          {t("chat.cancelEdit")}
        </button>
        <button
          type="button"
          onClick={save}
          disabled={!text.trim()}
          className="rounded-md bg-sky-600 px-2.5 py-1 font-medium text-white hover:bg-sky-500 disabled:opacity-40"
        >
          {t("chat.saveEdit")}
        </button>
      </div>
    </div>
  );
}

/** A small control that only appears on hover — the transcript stays quiet
 *  until you reach for it. `focus-within` keeps it reachable by keyboard. */
function HoverAction({
  label,
  onClick,
  align,
  children,
}: {
  label: string;
  onClick: () => void;
  align: "left" | "right";
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`mt-1 rounded p-1 text-zinc-600 opacity-0 transition-opacity hover:bg-zinc-800 hover:text-zinc-300 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-600 group-hover:opacity-100 ${
        align === "right" ? "self-end" : "self-start"
      }`}
    >
      {children}
    </button>
  );
}

export function MessageList({
  messages,
  streamingText,
  turns = [],
  onFork,
  onBranch,
  busy = false,
  subtitle,
}: Props) {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState<string | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [messages, streamingText]);

  if (messages.length === 0 && streamingText === null) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-8 text-center">
        <p className="text-sm text-zinc-500">{t("chat.empty")}</p>
        {subtitle && <p className="max-w-sm text-xs text-zinc-600">{subtitle}</p>}
      </div>
    );
  }

  const turnOf = (id?: string) => turns.find((t) => t.id === id);
  /** The user text of a turn — what a regenerate re-sends unchanged. */
  const questionOf = (turnId: string) =>
    messages.find((m) => m.turnId === turnId && m.role === "user")?.content ?? "";
  const canFork = onFork !== undefined && !busy;

  return (
    <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
      {messages.map((m) => {
        const turn = turnOf(m.turnId);

        if (m.role === "tool" && m.tool) return <ToolSpan key={m.id} span={m.tool} />;

        if (m.role === "user") {
          if (editing === m.id) {
            return (
              <div key={m.id} className="flex justify-end">
                <EditBox
                  initial={m.content}
                  onCancel={() => setEditing(null)}
                  onSave={(text) => {
                    setEditing(null);
                    // Fork from this turn's PARENT: the edited question is an
                    // alternative to this turn, not a continuation of it.
                    onFork?.(text, turn?.parent_turn_id ?? null);
                  }}
                />
              </div>
            );
          }
          return (
            <Bubble key={m.id} role="user" content={m.content}>
              {canFork && turn && (
                <HoverAction label={t("chat.edit")} align="right" onClick={() => setEditing(m.id)}>
                  <PencilIcon />
                </HoverAction>
              )}
              {turn && onBranch && (
                <BranchSwitcher siblings={turn.siblings} current={turn.id} onSwitch={onBranch} />
              )}
            </Bubble>
          );
        }

        return (
          <Bubble key={m.id} role={m.role} content={m.content}>
            {canFork && turn && (
              <HoverAction
                label={t("chat.regenerate")}
                align="left"
                // Same call as an edit, with the original question: a
                // regeneration is another alternative for the same turn.
                onClick={() => onFork?.(questionOf(turn.id), turn.parent_turn_id)}
              >
                <RetryIcon />
              </HoverAction>
            )}
          </Bubble>
        );
      })}
      {streamingText !== null && <Bubble role="assistant" content={streamingText || "…"} />}
      <div ref={bottomRef} />
    </div>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor">
      <path
        d="M13.5 3.5l3 3L7 16l-3.5.5L4 13l9.5-9.5z"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RetryIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor">
      <path
        d="M16 10a6 6 0 11-1.8-4.3M16 3v3h-3"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

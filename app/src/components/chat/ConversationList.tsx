// The conversation sidebar: new chat, switch, rename, delete.
//
// Delete is a two-step inline confirm rather than a modal — the rows are small
// and transient, and a modal for "throw away one chat" is heavier than the act
// deserves. There is no undo: the backend really removes the rows, and an
// honest undo would need a soft-delete column the schema can't gain.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationSummary } from "../../lib/types";

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  streamingId: string | null; // conversation currently being generated into
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4" aria-hidden>
      <path d="M12 5v14M5 12h14" strokeLinecap="round" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-3.5 w-3.5" aria-hidden>
      <path d="m14.5 5.5 4 4M3 21l.9-3.6a2 2 0 0 1 .5-.9l11.7-11.7a2 2 0 0 1 2.8 0l1.3 1.3a2 2 0 0 1 0 2.8L8.5 20.6a2 2 0 0 1-.9.5L3 21Z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-3.5 w-3.5" aria-hidden>
      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function ConversationList({
  conversations,
  activeId,
  streamingId,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: Props) {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId) inputRef.current?.select();
  }, [editingId]);

  const startRename = (c: ConversationSummary) => {
    setConfirmingId(null);
    setEditingId(c.id);
    setDraft(c.title ?? "");
  };

  const commitRename = () => {
    if (editingId && draft.trim()) onRename(editingId, draft);
    setEditingId(null);
  };

  return (
    <div className="flex h-full w-64 flex-col border-r border-zinc-800 bg-zinc-950">
      <div className="p-2">
        <button
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-lg border border-zinc-800 px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800"
        >
          <PlusIcon />
          {t("conversation.new")}
        </button>
      </div>

      <nav aria-label={t("conversation.listLabel")} className="flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-zinc-600">{t("conversation.empty")}</p>
        )}
        {conversations.map((c) => {
          const active = c.id === activeId;
          const title = c.title?.trim() || t("conversation.untitled");

          if (editingId === c.id) {
            return (
              <div key={c.id} className="px-1 py-0.5">
                <input
                  ref={inputRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  maxLength={80}
                  aria-label={t("conversation.renameLabel")}
                  className="w-full rounded-lg bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 outline-none ring-1 ring-sky-600"
                />
              </div>
            );
          }

          if (confirmingId === c.id) {
            return (
              <div
                key={c.id}
                className="my-0.5 flex items-center gap-2 rounded-lg bg-red-950/60 px-2.5 py-1.5"
              >
                <span className="flex-1 truncate text-xs text-red-200">
                  {t("conversation.confirmDelete")}
                </span>
                <button
                  onClick={() => {
                    setConfirmingId(null);
                    onDelete(c.id);
                  }}
                  className="rounded px-1.5 py-0.5 text-xs font-medium text-red-300 hover:bg-red-900/60"
                >
                  {t("conversation.delete")}
                </button>
                <button
                  onClick={() => setConfirmingId(null)}
                  className="rounded px-1.5 py-0.5 text-xs text-zinc-400 hover:bg-zinc-800"
                >
                  {t("conversation.cancel")}
                </button>
              </div>
            );
          }

          return (
            <div
              key={c.id}
              className={`group my-0.5 flex items-center gap-1 rounded-lg pr-1 transition-colors ${
                active ? "bg-zinc-800" : "hover:bg-zinc-900"
              }`}
            >
              <button
                onClick={() => onSelect(c.id)}
                aria-current={active ? "true" : undefined}
                title={title}
                className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left text-sm text-zinc-300"
              >
                {c.id === streamingId && (
                  <span
                    aria-label={t("conversation.generating")}
                    className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-sky-400"
                  />
                )}
                <span className="truncate">{title}</span>
              </button>
              {/* Always reachable by keyboard; only shown on hover/focus/active. */}
              <span
                className={`flex shrink-0 items-center gap-0.5 ${
                  active ? "" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100"
                }`}
              >
                <button
                  onClick={() => startRename(c)}
                  aria-label={t("conversation.rename")}
                  title={t("conversation.rename")}
                  className="rounded p-1.5 text-zinc-500 hover:bg-zinc-700 hover:text-zinc-200"
                >
                  <PencilIcon />
                </button>
                <button
                  onClick={() => setConfirmingId(c.id)}
                  aria-label={t("conversation.delete")}
                  title={t("conversation.delete")}
                  className="rounded p-1.5 text-zinc-500 hover:bg-red-900/60 hover:text-red-300"
                >
                  <TrashIcon />
                </button>
              </span>
            </div>
          );
        })}
      </nav>
    </div>
  );
}

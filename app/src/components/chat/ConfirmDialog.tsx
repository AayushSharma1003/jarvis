import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { ConfirmAnswer, ConfirmRequest } from "../../lib/types";

// The permission dialog. docs/security-model.md §1 originally called for a
// native OS dialog; §1 now records why this is in-app instead, and the short
// version is that native would not have removed the webview from the trust
// path — the webview is what would have had to ask Rust to show it. What
// actually carries the security is on the backend: the correlation id is minted
// there, it is single-use, and no answer at all means deny.
//
// What being in-app buys, and why it matters here:
//   - three choices instead of two (native ask() has no room for "this session")
//   - default focus on DENY, which no native dialog API here can express
//   - the full command in monospace, scrollable, so a long one is readable
//   - it can say WHY it is asking (M4.3's taint provenance goes in this block)

const RISK_STYLES: Record<string, string> = {
  ask: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  dangerous: "bg-red-500/10 text-red-300 ring-red-500/30",
};

export function ConfirmDialog({
  request,
  onAnswer,
}: {
  request: ConfirmRequest;
  onAnswer: (id: string, answer: ConfirmAnswer) => void;
}) {
  const { t } = useTranslation();
  const denyRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const answer = (a: ConfirmAnswer) => onAnswer(request.id, a);

  // Focus Deny, not just visually but actually — so Enter and Space on a
  // dialog that appeared under someone's fingers refuse rather than approve.
  // A `useEffect` rather than autoFocus because the dialog can be replaced by
  // the next queued request without unmounting.
  useEffect(() => {
    denyRef.current?.focus();
  }, [request.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        answer("deny");
        return;
      }
      if (e.key !== "Tab") return;
      // Trap focus: tabbing out would let a keystroke meant for the dialog
      // land on the composer behind it.
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>("button");
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  const args = Object.entries(request.arguments ?? {});
  // "Allow for this session" is never offered for a dangerous tool: §1 says
  // per-call confirmation, and per-call means per-call. The backend refuses to
  // remember one regardless — this only keeps the button from lying.
  const offerSession = request.risk !== "dangerous";

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        className="w-full max-w-sm rounded-xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl"
      >
        <div className="flex items-center gap-2">
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ${
              RISK_STYLES[request.risk] ?? RISK_STYLES.ask
            }`}
          >
            {t(`confirm.risk.${request.risk}`)}
          </span>
          {request.voice && (
            <span className="text-[11px] text-zinc-500">{t("confirm.fromVoice")}</span>
          )}
        </div>

        <h2 id="confirm-title" className="mt-2 text-sm font-medium text-zinc-100">
          {t("confirm.title", { name: request.name })}
        </h2>
        <p className="mt-1 text-xs text-zinc-400">{t("confirm.body")}</p>

        {args.length > 0 && (
          <dl className="mt-3 max-h-40 overflow-y-auto rounded-lg bg-zinc-950/70 px-3 py-2">
            {args.map(([key, value]) => (
              <div key={key} className="py-0.5">
                <dt className="font-mono text-[10px] uppercase text-zinc-600">{key}</dt>
                <dd
                  data-selectable
                  className="whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-zinc-300"
                >
                  {typeof value === "string" ? value : JSON.stringify(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <div className="mt-4 flex flex-col gap-2">
          <button
            ref={denyRef}
            onClick={() => answer("deny")}
            className="rounded-lg bg-zinc-100 px-3 py-2 text-sm font-medium text-zinc-900 outline-none transition-colors hover:bg-white focus-visible:ring-2 focus-visible:ring-zinc-400"
          >
            {t("confirm.deny")}
          </button>
          <div className="flex gap-2">
            <button
              onClick={() => answer("once")}
              className="flex-1 rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-300 outline-none transition-colors hover:bg-zinc-700 focus-visible:ring-2 focus-visible:ring-zinc-500"
            >
              {t("confirm.once")}
            </button>
            {offerSession && (
              <button
                onClick={() => answer("session")}
                title={t("confirm.sessionHint")}
                className="flex-1 rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-300 outline-none transition-colors hover:bg-zinc-700 focus-visible:ring-2 focus-visible:ring-zinc-500"
              >
                {t("confirm.session")}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

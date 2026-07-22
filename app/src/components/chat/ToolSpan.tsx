import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ToolSpanData } from "../../lib/types";

// Collapsed by default. A tool span is context, not the answer — expanded by
// default it would push the reply off-screen on every tool turn. But it must be
// *reachable*: once tainted content can steer the assistant (phase 4.3+), the
// user is entitled to see exactly what it was told.

function WrenchIcon({ ok }: { ok: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      className={`h-3.5 w-3.5 shrink-0 ${ok ? "text-zinc-500" : "text-amber-500"}`}
      aria-hidden
    >
      {ok ? (
        <path
          d="M14.7 6.3a4 4 0 0 1-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.1-2.1z"
          strokeLinejoin="round"
        />
      ) : (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v4.5M12 16h.01" strokeLinecap="round" />
        </>
      )}
    </svg>
  );
}

export function ToolSpan({ span }: { span: ToolSpanData }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const args = Object.keys(span.arguments ?? {}).length
    ? JSON.stringify(span.arguments)
    : "";

  // Backend sends codes only; every string below comes from i18n.
  const label = span.ok
    ? t("tool.used", { name: span.name })
    : span.name
      ? t("tool.failed", { name: span.name })
      : t("tool.failedUnknown");
  const reason = span.ok ? "" : t(`tool.code.${span.code}`, { defaultValue: t("tool.code.UNKNOWN") });

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] text-xs">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
        >
          <WrenchIcon ok={span.ok} />
          <span>{label}</span>
          {!span.ok && <span className="text-amber-600/90">· {reason}</span>}
        </button>
        {open && (
          <div className="mt-1 space-y-1 rounded-lg bg-zinc-950/60 px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-400">
            {args && (
              <div data-selectable className="whitespace-pre-wrap break-all">
                <span className="text-zinc-600">{t("tool.arguments")} </span>
                {args}
              </div>
            )}
            {span.content && (
              <div data-selectable className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words">
                <span className="text-zinc-600">{t("tool.result")} </span>
                {span.content}
              </div>
            )}
            {!args && !span.content && <div className="text-zinc-600">{t("tool.noDetail")}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

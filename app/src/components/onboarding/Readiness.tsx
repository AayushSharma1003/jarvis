// The first-run gate. Shown in place of the message list when the backend
// reports that something is missing (`system.readiness` → ready: false).
//
// Deliberately not a modal: the sidebar and old conversations stay reachable,
// because "Ollama isn't running" is no reason to lock someone out of their own
// history. Deliberately not a downloader either — a developer with a terminal
// open is better served by the exact command than by a progress bar we'd have
// to build a cancel path for. Model download UI belongs with the installer.

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ReadinessCheck } from "../../lib/types";

/** The one command that fixes each code, or none if it isn't a command. */
const FIX_COMMAND: Record<string, string> = {
  NO_MODELS: "ollama pull llama3.2:3b",
  VOICE_MODELS_MISSING: "uv run python ../scripts/fetch_models.py",
  WAKE_MODELS_MISSING: "uv run python ../scripts/fetch_models.py",
};

function FixCommand({ command }: { command: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const codeRef = useRef<HTMLElement>(null);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access gets refused when the document isn't focused (and in
      // locked-down webviews). Select the text so ⌘C still gets them there.
      const node = codeRef.current;
      if (!node) return;
      const range = document.createRange();
      range.selectNodeContents(node);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
  };

  return (
    <div className="mt-1.5 flex items-center gap-2">
      <code
        ref={codeRef}
        data-selectable
        className="min-w-0 flex-1 truncate rounded-md bg-zinc-950/70 px-2 py-1 font-mono text-[11px] text-zinc-300"
      >
        {command}
      </code>
      <button
        onClick={() => void copy()}
        className="shrink-0 rounded-md bg-zinc-700 px-2 py-1 text-[11px] text-zinc-200 transition-colors hover:bg-zinc-600"
      >
        {copied ? t("readiness.copied") : t("readiness.copy")}
      </button>
    </div>
  );
}

function Row({ check }: { check: ReadinessCheck }) {
  const { t } = useTranslation();
  const failed = check.status === "fail";
  const key = `readiness.code.${check.code}`;
  const command = check.code ? FIX_COMMAND[check.code] : undefined;
  return (
    <li className="flex gap-3">
      <span
        aria-hidden
        className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
          failed ? "bg-red-400" : "bg-amber-400"
        }`}
      />
      <div className="min-w-0 flex-1">
        <p className={failed ? "text-zinc-200" : "text-zinc-400"}>
          {t(key, { ...check.data, defaultValue: check.code ?? "" })}
        </p>
        {command && <FixCommand command={command} />}
        {check.id === "microphone" && (
          <p className="mt-1 text-[11px] text-zinc-500">{t("readiness.micPermissionNote")}</p>
        )}
      </div>
    </li>
  );
}

export function Readiness({
  checks,
  onRecheck,
}: {
  checks: ReadinessCheck[];
  onRecheck: () => void;
}) {
  const { t } = useTranslation();
  // Failures first: they are what's actually blocking the app.
  const problems = checks
    .filter((c) => c.status !== "ok")
    .sort((a, b) => Number(b.status === "fail") - Number(a.status === "fail"));
  if (problems.length === 0) return null;

  return (
    <div className="flex flex-1 items-center justify-center overflow-y-auto px-6 py-4">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-950/40 p-5">
        <h2 className="text-sm font-medium text-zinc-100">{t("readiness.title")}</h2>
        <ul className="mt-3 space-y-3 text-xs leading-relaxed">
          {problems.map((c) => (
            <Row key={c.id} check={c} />
          ))}
        </ul>
        <button
          onClick={onRecheck}
          className="mt-4 rounded-lg bg-zinc-800 px-3 py-1.5 text-xs text-zinc-200 transition-colors hover:bg-zinc-700"
        >
          {t("readiness.recheck")}
        </button>
      </div>
    </div>
  );
}

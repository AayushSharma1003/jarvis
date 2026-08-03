// The first-run gate. Shown in place of the message list when the backend
// reports that something is missing (`system.readiness` → ready: false).
//
// Deliberately not a modal: the sidebar and old conversations stay reachable,
// because "Ollama isn't running" is no reason to lock someone out of their own
// history.
//
// It IS a downloader, and it had to become one. This file used to argue that
// "a developer with a terminal open is better served by the exact command" —
// true, and irrelevant to everyone who installs a release. Those users have no
// terminal in the loop, no `scripts/` (it isn't in the bundle) and no CLI (the
// frozen sidecar's entrypoint is the server), so the command shown here named
// a repo they had never cloned. Voice was unreachable for every one of them.
// The fetch still only ever happens on a click — see assets.py.

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { DOWNLOADABLE } from "../../lib/readiness";
import type { ReadinessCheck } from "../../lib/types";

/** The one command that fixes each code, or none if it isn't a command.
 *  Ollama stays a command: it's a separate program we deliberately don't
 *  bundle or install on the user's behalf. */
const FIX_COMMAND: Record<string, string> = {
  NO_MODELS: "ollama pull llama3.2:3b",
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

function DownloadModels({
  progress,
  failed,
  onFetch,
}: {
  progress: { name: string; done: number; total: number } | null;
  failed: string[] | null;
  onFetch: () => void;
}) {
  const { t } = useTranslation();
  const running = progress !== null;
  const pct =
    progress && progress.total > 0 ? Math.round((100 * progress.done) / progress.total) : 0;

  return (
    <div className="mt-2">
      <button
        onClick={onFetch}
        disabled={running}
        className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-default disabled:bg-zinc-700 disabled:text-zinc-400"
      >
        {running ? t("readiness.downloading") : t("readiness.download")}
      </button>
      {running && (
        <div className="mt-2">
          <div className="h-1 w-full overflow-hidden rounded-full bg-zinc-800">
            <div
              className="h-full bg-sky-500 transition-[width] duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="mt-1 font-mono text-[11px] text-zinc-500">
            {progress.name} · {pct}%
          </p>
        </div>
      )}
      {!running && failed && failed.length > 0 && (
        <p className="mt-1.5 text-[11px] text-amber-400">
          {t("readiness.downloadFailed", { names: failed.join(", ") })}
        </p>
      )}
      {!running && <p className="mt-1.5 text-[11px] text-zinc-500">{t("readiness.downloadSize")}</p>}
    </div>
  );
}

// The download control belongs to the LIST, not to a row. One `assets.fetch`
// downloads everything missing, so a button per row meant two identical buttons
// and two identical "About 500 MB, once" lines whenever both the voice and wake
// groups were absent — which is every fresh install.
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

/**
 * The same rows, in a strip above the composer, for warnings the blocking gate
 * will never show because it isn't up.
 *
 * This exists because the gate renders only on a *failure*, and the one thing a
 * new user most needs — "Download voice models" — hangs off a *warning*. A
 * person who installed Ollama correctly therefore got `ready: true`, no gate,
 * and no way to obtain voice at all: the button was reachable only by users
 * whose setup was broken in some other way. See lib/readiness.ts.
 *
 * It reuses `Row`, so there is one implementation of a readiness row and two
 * callers — the same reason `cli.py`'s `_print_declaration` has one copy: a
 * second entry point must not be able to quietly start showing less.
 */
export function ReadinessAdvisory({
  checks,
  assetFetch = null,
  assetFetchFailed = null,
  onFetch = () => {},
}: {
  checks: ReadinessCheck[];
  assetFetch?: { name: string; done: number; total: number } | null;
  assetFetchFailed?: string[] | null;
  onFetch?: () => void;
}) {
  if (checks.length === 0) return null;
  return (
    <div className="mx-4 mb-2 rounded-lg border border-zinc-800 bg-zinc-900/70 px-3 py-2">
      <ul className="space-y-2 text-xs leading-relaxed">
        {checks.map((c) => (
          <Row key={c.id} check={c} />
        ))}
      </ul>
      {checks.some((c) => c.code !== undefined && DOWNLOADABLE.has(c.code)) && (
        <div className="pl-6">
          <DownloadModels progress={assetFetch} failed={assetFetchFailed} onFetch={onFetch} />
        </div>
      )}
    </div>
  );
}

export function Readiness({
  checks,
  onRecheck,
  assetFetch = null,
  assetFetchFailed = null,
  onFetch = () => {},
}: {
  checks: ReadinessCheck[];
  onRecheck: () => void;
  assetFetch?: { name: string; done: number; total: number } | null;
  assetFetchFailed?: string[] | null;
  onFetch?: () => void;
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
        {problems.some((c) => c.code !== undefined && DOWNLOADABLE.has(c.code)) && (
          <div className="pl-6">
            <DownloadModels progress={assetFetch} failed={assetFetchFailed} onFetch={onFetch} />
          </div>
        )}
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

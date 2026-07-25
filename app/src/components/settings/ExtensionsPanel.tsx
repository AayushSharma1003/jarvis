import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { errorText } from "../../i18n";
import { useConversation } from "../../state/conversation";
import type { ExtensionInfo } from "../../lib/types";

// The extension approval panel (docs/security-model.md §5, M5.2). It is the
// GUI counterpart of `jarvis extensions`, and it carries the same property the
// CLI does: approval is two steps, never one click. A row shows what an
// extension IS; approving opens a detail card showing what it DECLARES and the
// plain fact that its code runs unsandboxed — and only there are Approve and
// Cancel offered. A one-click Approve in a list row is approving without
// reading, which is exactly what §5 exists to prevent.
//
// The digest shown in the card is echoed back on approve so the backend can
// refuse (EXTENSION_CHANGED) if the folder changed since it was displayed.

const RISK_STYLES: Record<string, string> = {
  safe: "bg-zinc-700/40 text-zinc-300 ring-zinc-600/40",
  ask: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  dangerous: "bg-red-500/10 text-red-300 ring-red-500/30",
};

const STATUS_STYLES: Record<string, string> = {
  approved: "text-emerald-400",
  pending: "text-amber-400",
  changed: "text-amber-400",
  unsupported_platform: "text-zinc-500",
  invalid: "text-red-400",
};

/** How many installed extensions still want the user's attention — drives the
 *  header badge. Anything not yet approved (or approved-then-changed) counts. */
export function pendingReviewCount(extensions: ExtensionInfo[]): number {
  return extensions.filter((e) => e.status === "pending" || e.status === "changed").length;
}

export function ExtensionsPanel({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const s = useConversation();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  // Which extension's detail card is open for approval; null = the list.
  const [reviewing, setReviewing] = useState<string | null>(null);

  // A fresh survey each time the panel opens: a folder edited while the app was
  // running should show as "changed" without a reconnect.
  useEffect(() => {
    s.listExtensions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      // Escape backs out of the detail card first, then closes the panel — so
      // it never approves and never skips a step.
      if (reviewing) setReviewing(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [reviewing, onClose]);

  const detail = reviewing ? s.extensions.find((e) => e.name === reviewing) : undefined;

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ext-title"
        className="flex max-h-[80vh] w-full max-w-md flex-col rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <h2 id="ext-title" className="text-sm font-medium text-zinc-100">
            {t("extension.title")}
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-xs text-zinc-400 outline-none transition-colors hover:bg-zinc-800 hover:text-zinc-200 focus-visible:ring-2 focus-visible:ring-zinc-500"
          >
            {t("extension.close")}
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {detail ? (
            <DetailCard
              info={detail}
              error={s.extensionError}
              onApprove={() => {
                s.approveExtension(detail.name, detail.digest);
                setReviewing(null);
              }}
              onCancel={() => {
                s.clearExtensionError();
                setReviewing(null);
              }}
            />
          ) : (
            <ExtensionList
              extensions={s.extensions}
              error={s.extensionError}
              onReview={(name) => {
                s.clearExtensionError();
                setReviewing(name);
              }}
              onRevoke={s.revokeExtension}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ExtensionList({
  extensions,
  error,
  onReview,
  onRevoke,
}: {
  extensions: ExtensionInfo[];
  error: string | null;
  onReview: (name: string) => void;
  onRevoke: (name: string) => void;
}) {
  const { t } = useTranslation();
  // Revoke is two-step so the caveat — its tools stop, but code it already ran
  // stays in memory until restart — is read before it happens, not after. Same
  // inline-confirm shape as deleting a conversation.
  const [confirmingRevoke, setConfirmingRevoke] = useState<string | null>(null);

  if (extensions.length === 0) {
    return (
      <div className="py-8 text-center">
        <p className="text-sm text-zinc-400">{t("extension.empty")}</p>
        <p className="mt-1 text-xs text-zinc-600">{t("extension.emptyHint")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {error && (
        <p className="rounded-lg bg-red-950/60 px-3 py-2 text-xs text-red-300">
          {errorText(error)}
        </p>
      )}
      {extensions.map((e) => {
        // Approved-but-didn't-load and changed both need a word: the first
        // implies a tool set the user doesn't have, the second that the code
        // was edited out from under its approval.
        const note =
          e.status === "approved" && !e.loaded
            ? t("extension.notLoadedNote")
            : e.status === "changed"
              ? t("extension.changedNote")
              : null;
        return (
          <div key={e.name} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-200">
                {e.name}
                {e.version && <span className="ml-1.5 text-xs text-zinc-600">{e.version}</span>}
              </span>
              <span className={`shrink-0 text-xs ${STATUS_STYLES[e.status] ?? "text-zinc-500"}`}>
                {t(`extension.status.${e.status}`)}
              </span>
            </div>
            {e.code && (
              <p className="mt-1 text-xs text-zinc-500">{t(`extension.code.${e.code}`)}</p>
            )}
            {note && <p className="mt-1 text-xs text-amber-400/80">{note}</p>}
            {confirmingRevoke === e.name ? (
              <div className="mt-2 rounded-lg bg-zinc-800/60 p-2">
                <p className="text-xs text-zinc-400">{t("extension.revokeCaveat")}</p>
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() => {
                      setConfirmingRevoke(null);
                      onRevoke(e.name);
                    }}
                    className="rounded px-2 py-0.5 text-xs font-medium text-red-300 outline-none hover:bg-red-900/60 focus-visible:ring-2 focus-visible:ring-red-500"
                  >
                    {t("extension.revoke")}
                  </button>
                  <button
                    onClick={() => setConfirmingRevoke(null)}
                    className="rounded px-2 py-0.5 text-xs text-zinc-400 outline-none hover:bg-zinc-700 focus-visible:ring-2 focus-visible:ring-zinc-500"
                  >
                    {t("extension.cancel")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-2 flex gap-2">
                {/* Review = approve these bytes; shown while there is something
                    to approve. Revoke = withdraw the approval record; shown
                    whenever one exists — `changed` still has the old record (and
                    the old code running), so it offers both. */}
                {(e.status === "pending" || e.status === "changed") && (
                  <button
                    onClick={() => onReview(e.name)}
                    className="rounded-lg bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-900 outline-none transition-colors hover:bg-white focus-visible:ring-2 focus-visible:ring-zinc-400"
                  >
                    {t("extension.review")}
                  </button>
                )}
                {(e.status === "approved" || e.status === "changed") && (
                  <button
                    onClick={() => setConfirmingRevoke(e.name)}
                    className="rounded-lg bg-zinc-800 px-2.5 py-1 text-xs text-zinc-300 outline-none transition-colors hover:bg-zinc-700 focus-visible:ring-2 focus-visible:ring-zinc-500"
                  >
                    {t("extension.revoke")}
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function DetailCard({
  info,
  error,
  onApprove,
  onCancel,
}: {
  info: ExtensionInfo;
  error: string | null;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Cancel takes focus, matching ConfirmDialog: a card that appeared under
  // someone's fingers must not approve on a stray keypress.
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  const Row = ({ label, value }: { label: string; value: string }) => (
    <div className="flex gap-2 py-0.5 text-xs">
      <dt className="w-24 shrink-0 text-zinc-600">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-zinc-300">{value}</dd>
    </div>
  );

  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium text-zinc-100">{info.name}</span>
        {info.version && <span className="text-xs text-zinc-600">{info.version}</span>}
      </div>
      {info.description && (
        <p className="mt-1 text-xs text-zinc-400">{info.description}</p>
      )}

      <dl className="mt-3 rounded-lg bg-zinc-950/70 px-3 py-2">
        <Row
          label={t("extension.platforms")}
          value={info.platforms.length ? info.platforms.join(", ") : t("extension.anyPlatform")}
        />
        <Row
          label={t("extension.osAccess")}
          value={info.os_permissions.length ? info.os_permissions.join(", ") : t("extension.osNone")}
        />
        <Row
          label={t("extension.network")}
          value={info.network ? t("extension.networkYes") : t("extension.networkNo")}
        />
      </dl>

      <p className="mt-3 text-xs font-medium text-zinc-500">{t("extension.toolsItAdds")}</p>
      <div className="mt-1 space-y-1">
        {info.tools.map((tool) => (
          <div
            key={tool.name}
            className="flex items-center gap-2 rounded-lg bg-zinc-950/70 px-3 py-1.5"
          >
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300">
              {tool.name}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ${
                RISK_STYLES[tool.risk] ?? RISK_STYLES.ask
              }`}
            >
              {t(`confirm.risk.${tool.risk}`)}
            </span>
          </div>
        ))}
      </div>

      {/* §5's honest half, verbatim in intent from the CLI prompt: the
          permissions above are what it SAYS it needs, not a boundary. */}
      <p className="mt-3 rounded-lg bg-amber-500/10 px-2.5 py-2 text-xs leading-relaxed text-amber-200/90 ring-1 ring-amber-500/20">
        {t("extension.runsAsYou")}
      </p>
      <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
        {t("extension.coversFiles", { digest: `${info.digest.slice(0, 12)}…` })}
      </p>

      {error && (
        <p className="mt-2 rounded-lg bg-red-950/60 px-3 py-2 text-xs text-red-300">
          {errorText(error)}
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <button
          ref={cancelRef}
          onClick={onCancel}
          className="flex-1 rounded-lg bg-zinc-800 px-3 py-2 text-sm text-zinc-300 outline-none transition-colors hover:bg-zinc-700 focus-visible:ring-2 focus-visible:ring-zinc-500"
        >
          {t("extension.cancel")}
        </button>
        <button
          onClick={onApprove}
          className="flex-1 rounded-lg bg-zinc-100 px-3 py-2 text-sm font-medium text-zinc-900 outline-none transition-colors hover:bg-white focus-visible:ring-2 focus-visible:ring-zinc-400"
        >
          {t("extension.approve")}
        </button>
      </div>
    </div>
  );
}

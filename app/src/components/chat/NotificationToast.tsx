import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { TOAST_DISMISS_MS, notificationText } from "../../lib/notifications";
import type { JarvisNotification } from "../../lib/types";

// Toasts for things that happened without being asked for — an extension's
// timer firing (M5.4). Everything else in this app renders in response to
// something the user did; this is the only surface that interrupts.
//
// Deliberately NOT a dialog, and the difference is the point:
//   - it never takes focus, so a toast arriving mid-sentence cannot swallow a
//     keystroke meant for the composer (the ConfirmDialog beside it does the
//     opposite, on purpose — a permission prompt SHOULD steal focus)
//   - it dismisses itself, because nobody should have to acknowledge a timer
//   - it cannot cover the confirm dialog: ChatView renders that after this
//
// The text is extension-influenced (`data` is whatever the extension passed,
// via a label the user spoke). React escapes it, and host.py truncated it
// before it left the backend, so the remaining job here is only to keep a long
// one from breaking the layout.

export function NotificationToasts({
  notifications,
  onDismiss,
}: {
  notifications: JarvisNotification[];
  onDismiss: (id: string) => void;
}) {
  if (notifications.length === 0) return null;
  return (
    // `pointer-events-none` on the stack, re-enabled per toast: the empty
    // space beside a toast must stay clickable, or a toast in the corner
    // silently eats clicks on whatever is under it for eight seconds.
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-40 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
      // Announced by screen readers without moving focus. `polite` rather
      // than `assertive`: a timer is worth saying, not worth interrupting.
      role="status"
      aria-live="polite"
    >
      {notifications.map((n) => (
        <Toast key={n.id} notification={n} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function Toast({
  notification,
  onDismiss,
}: {
  notification: JarvisNotification;
  onDismiss: (id: string) => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    const timer = setTimeout(() => onDismiss(notification.id), TOAST_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [notification.id, onDismiss]);

  return (
    <div className="pointer-events-auto flex items-start gap-3 rounded-lg bg-zinc-800 px-3 py-2.5 text-sm text-zinc-100 shadow-lg ring-1 ring-zinc-700">
      <div className="min-w-0 flex-1">
        <p className="break-words">{notificationText(notification)}</p>
        <p className="mt-0.5 truncate text-xs text-zinc-500">{notification.source}</p>
      </div>
      <button
        type="button"
        onClick={() => onDismiss(notification.id)}
        aria-label={t("notification.dismiss")}
        className="-mr-1 -mt-0.5 shrink-0 rounded p-1 text-zinc-500 hover:bg-zinc-700 hover:text-zinc-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
      >
        <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor">
          <path d="M6 6l8 8M14 6l-8 8" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

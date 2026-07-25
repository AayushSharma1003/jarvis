import i18n from "../i18n";
import type { JarvisNotification } from "./types";

// How many toasts to keep on screen. A notification is transient and stacking
// more than a few turns a reminder into a wall — and the backend already caps
// the rate that can reach us (extensions/host.py).
export const MAX_TOASTS = 4;

// How long a toast stays before fading itself out. Long enough to read a
// sentence, short enough that it does not become furniture.
export const TOAST_DISMISS_MS = 8_000;

/**
 * The sentence for one notification, in the user's language.
 *
 * Backend codes in, wording out — the i18n rule. The same function feeds the
 * toast and the spoken line, so what Jarvis says and what the toast reads are
 * the same sentence by construction rather than by two people remembering.
 *
 * An unknown code is not an error: any approved extension can emit any code,
 * and third-party ones will emit codes this build has never heard of. Falling
 * back to a neutral line is right — rendering a raw `TIMER_FOO_BAR` at the
 * user would be worse than saying an extension had something to say.
 */
export function notificationText(notification: JarvisNotification): string {
  const key = `notification.code.${notification.code}`;
  if (i18n.exists(key)) {
    return i18n.t(key, notification.data as Record<string, unknown>);
  }
  return i18n.t("notification.unknown", {
    source: notification.source,
    code: notification.code,
  });
}

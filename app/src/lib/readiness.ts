// What a `system.readiness` payload entitles the UI to show.
//
// This lived inline in ChatView as a single `blocked` boolean, and that is how
// the download button went missing for the users who needed it most. The gate
// renders only when something FAILS, missing voice models are a WARNING (text
// chat works fine without them), and the only "Download voice models" button in
// the app was inside the gate — so a user who had installed Ollama correctly
// got `ready: true`, no gate, and no way to obtain voice at all. The button
// existed for exactly the people who could never see it.
//
// Pulled out here so the rule is a function with tests instead of a condition
// buried in a render tree. There is nothing to render without a decision.

import type { ReadinessCheck } from "./types";

/** Codes the in-app downloader can actually fix. */
export const DOWNLOADABLE = new Set(["VOICE_MODELS_MISSING", "WAKE_MODELS_MISSING"]);

/**
 * Does the gate replace the conversation? Only for a *failure* — a warning
 * degrades something without stopping the user talking to Jarvis, and locking
 * someone out of their own history over a missing microphone is not a gate,
 * it's a wall.
 */
export function isBlocked(readiness: ReadinessCheck[] | null, ready: boolean): boolean {
  return readiness !== null && !ready;
}

/**
 * Rows worth surfacing *without* taking the screen — warnings the user can act
 * on, which the blocking gate will never show them because it isn't up.
 *
 * Empty while `isBlocked`, because the gate is already saying all of this in
 * full and a banner underneath it would be the same sentence twice.
 */
export function advisoryChecks(
  readiness: ReadinessCheck[] | null,
  ready: boolean,
): ReadinessCheck[] {
  if (readiness === null || isBlocked(readiness, ready)) return [];
  // Filtered on the code alone, not on `status === "warn"`. That looked like
  // the obvious guard and it is dead: the backend attaches a code only to a row
  // that is warning or failing (readiness.py's `_check`), and a failing row
  // means `ready` is false, which the line above has already returned on. A
  // mutation removing the status test came back NOT CAUGHT, so it went, rather
  // than a test being invented to justify it.
  return readiness.filter((c) => c.code !== undefined && isAdvisory(c.code));
}

/**
 * Which warnings earn a banner. Deliberately a short allowlist rather than
 * "every warning": `TOOLS_OPTIN` and `TOOLS_UNSUPPORTED` describe a considered
 * choice about the user's model that they cannot fix from here, and nagging
 * about it on every launch is the confirmation fatigue the security model warns
 * about, with no attacker in it.
 *
 * These three are different in kind — each is a thing the user *wants*, that is
 * switched off, that they can turn on and would otherwise never learn about.
 */
function isAdvisory(code: string): boolean {
  return DOWNLOADABLE.has(code) || code === "CONFIG_PARSE_ERROR" || code === "CONFIG_INVALID_VALUE";
}

/** Is the "Download voice models" button reachable for this payload? */
export function offersDownload(readiness: ReadinessCheck[] | null, ready: boolean): boolean {
  const rows = isBlocked(readiness, ready) ? (readiness ?? []) : advisoryChecks(readiness, ready);
  return rows.some((c) => c.code !== undefined && DOWNLOADABLE.has(c.code));
}

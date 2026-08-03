// The first frontend tests in the project, and they exist because of the bug
// they pin: for four release candidates the "Download voice models" button was
// unreachable by every user whose setup was correct.
//
// Nothing in the backend suite could see it. `system.readiness` was right —
// missing voice models ARE a warning, text chat really does work without them —
// and the button really was wired to the fetch. The defect was entirely in
// which UI state each one lived in, and the UI had no tests.

import { describe, expect, it } from "vitest";
import { advisoryChecks, isBlocked, offersDownload } from "./readiness";
import type { ReadinessCheck } from "./types";

/** What a fresh install with Ollama installed and a model pulled reports —
 *  captured from the shipped v0.1.0-rc6 sidecar against an empty data dir. */
const FRESH_MACHINE: ReadinessCheck[] = [
  { id: "llm", status: "ok" },
  { id: "model", status: "ok" },
  { id: "tools", status: "warn", code: "TOOLS_OPTIN" },
  { id: "voice_models", status: "warn", code: "VOICE_MODELS_MISSING" },
  { id: "wake_models", status: "warn", code: "WAKE_MODELS_MISSING" },
  { id: "microphone", status: "ok" },
];

const NO_OLLAMA: ReadinessCheck[] = [
  { id: "llm", status: "fail", code: "OLLAMA_UNREACHABLE" },
  { id: "voice_models", status: "warn", code: "VOICE_MODELS_MISSING" },
  { id: "wake_models", status: "warn", code: "WAKE_MODELS_MISSING" },
  { id: "microphone", status: "ok" },
];

const ALL_GOOD: ReadinessCheck[] = [
  { id: "llm", status: "ok" },
  { id: "model", status: "ok" },
  { id: "voice_models", status: "ok" },
  { id: "wake_models", status: "ok" },
  { id: "microphone", status: "ok" },
];

describe("the download button is reachable by the people who need it", () => {
  it("offers the download on a correctly set up machine with no voice models", () => {
    // THE regression. `ready` is true here — nothing is failing — so the
    // blocking gate never renders, and before this the button rendered only
    // inside that gate. A user who followed the README exactly ended up with
    // working text chat and no way to ever turn voice on.
    expect(isBlocked(FRESH_MACHINE, true)).toBe(false);
    expect(offersDownload(FRESH_MACHINE, true)).toBe(true);
  });

  it("still offers the download from the blocking gate when Ollama is missing", () => {
    // The path that did work, kept working: the gate is up for the LLM
    // failure and carries the download for the warnings alongside it.
    expect(isBlocked(NO_OLLAMA, false)).toBe(true);
    expect(offersDownload(NO_OLLAMA, false)).toBe(true);
  });

  it("offers nothing once the models are on disk", () => {
    expect(offersDownload(ALL_GOOD, true)).toBe(false);
    expect(advisoryChecks(ALL_GOOD, true)).toEqual([]);
  });

  it("offers nothing before the first readiness payload arrives", () => {
    expect(offersDownload(null, true)).toBe(false);
    expect(isBlocked(null, true)).toBe(false);
  });
});

describe("what earns a banner, and what does not", () => {
  it("stays quiet while the blocking gate is up", () => {
    // The gate already explains every row in full. A banner underneath it is
    // the same sentence twice — the reasoning ChatView already applies to the
    // error banner.
    expect(advisoryChecks(NO_OLLAMA, false)).toEqual([]);
  });

  it("does not nag about a model that cannot use tools", () => {
    // TOOLS_OPTIN is a considered decision about the user's model, not
    // something they can act on from a banner, and re-raising it on every
    // launch is confirmation fatigue with no attacker in it.
    const codes = advisoryChecks(FRESH_MACHINE, true).map((c) => c.code);
    expect(codes).not.toContain("TOOLS_OPTIN");
    expect(codes).toEqual(["VOICE_MODELS_MISSING", "WAKE_MODELS_MISSING"]);
  });

  it("surfaces an unreadable config file", () => {
    // Failing closed costs the user their filesystem roots. If that is
    // invisible, Jarvis just looks broken.
    const checks: ReadinessCheck[] = [
      ...ALL_GOOD,
      { id: "config", status: "warn", code: "CONFIG_PARSE_ERROR" },
    ];
    expect(advisoryChecks(checks, true).map((c) => c.code)).toEqual(["CONFIG_PARSE_ERROR"]);
  });

  it("ignores rows that are merely ok", () => {
    expect(advisoryChecks(ALL_GOOD, true)).toEqual([]);
  });
});

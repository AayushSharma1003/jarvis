// Shared sphere-state definitions: the WebGL orb and the 2D fallback read the
// SAME motion parameters, so falling back changes fidelity, never feel.

import type { VoiceState } from "../../lib/types";

export type SphereVisualState = "idle" | "listening" | "thinking" | "speaking";

/** loading/transcribing are backend pipeline states; visually they're "thinking". */
export function visualStateOf(v: VoiceState): SphereVisualState {
  switch (v) {
    case "listening":
      return "listening";
    case "speaking":
      return "speaking";
    case "loading":
    case "transcribing":
    case "thinking":
      return "thinking";
    default:
      return "idle";
  }
}

export const PALETTE = {
  cyan: "#22d3ee",
  blue: "#3b82f6",
  purple: "#a855f7",
  bg: "#0d0d16",
} as const;

export interface StateParams {
  amp: number; // base waveform amplitude
  gain: number; // how much the live audio level adds on top
  bright: number; // overall brightness multiplier
  gather: number; // 0–1 pull toward the center/upward (listening)
  rot: number; // slow orbit speed, rad/s (thinking = swirl)
  speed: number; // noise scroll speed
}

export const STATE_PARAMS: Record<SphereVisualState, StateParams> = {
  idle: { amp: 0.16, gain: 0.0, bright: 0.6, gather: 0, rot: 0.06, speed: 0.5 },
  listening: { amp: 0.2, gain: 0.55, bright: 0.95, gather: 0.45, rot: 0.12, speed: 0.9 },
  thinking: { amp: 0.28, gain: 0.0, bright: 0.8, gather: 0.15, rot: 0.9, speed: 1.6 },
  speaking: { amp: 0.22, gain: 0.95, bright: 1.1, gather: 0, rot: 0.2, speed: 1.1 },
};

/** Frame-rate-independent exponential approach: returns the new value. */
export function approach(value: number, target: number, rate: number, dt: number): number {
  return value + (target - value) * Math.min(1, rate * dt);
}

/** cyan→blue→purple gradient at t∈[0,1], as [r,g,b] in 0–1. */
export function gradientRgb(t: number): [number, number, number] {
  const c: [number, number, number] = [0x22 / 255, 0xd3 / 255, 0xee / 255];
  const b: [number, number, number] = [0x3b / 255, 0x82 / 255, 0xf6 / 255];
  const p: [number, number, number] = [0xa8 / 255, 0x55 / 255, 0xf7 / 255];
  const mix = (u: [number, number, number], v: [number, number, number], k: number) =>
    [u[0] + (v[0] - u[0]) * k, u[1] + (v[1] - u[1]) * k, u[2] + (v[2] - u[2]) * k] as [
      number,
      number,
      number,
    ];
  return t < 0.5 ? mix(c, b, t * 2) : mix(b, p, t * 2 - 1);
}

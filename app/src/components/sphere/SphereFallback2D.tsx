// The mandatory canvas-2D orb (docs/design/sphere.md): WebKitGTK on Linux has
// flaky WebGL, and weak GPUs trip the Sphere's frame-time watchdog — either
// way, this takes over. Same palette, same four states, same STATE_PARAMS as
// the WebGL orb, so the swap changes fidelity, never behavior.
//
// Cost model: ~9 rows × 72 dots plus a rim and a floor ellipse at 30 fps —
// comfortably nothing, which is the point.

import { useEffect, useRef } from "react";
import { approach, gradientRgb, PALETTE, STATE_PARAMS, type SphereVisualState } from "./params";

interface Props {
  visualState: SphereVisualState;
  getLevel: () => number;
}

const FPS = 30;
const ROWS = 9;
const COLS = 72;
const MAX_DPR = 1.5;

// Per-column colors precomputed once (cyan→blue→purple across x).
const LUT: string[] = Array.from({ length: COLS }, (_, i) => {
  const [r, g, b] = gradientRgb(i / (COLS - 1));
  return `rgba(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)},`;
});

export function SphereFallback2D({ visualState, getLevel }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(visualState);
  stateRef.current = visualState;
  const levelRef = useRef(getLevel);
  levelRef.current = getLevel;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const size = { w: 0, h: 0 };
    const applySize = () => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w === 0 || h === 0 || (w === size.w && h === size.h)) return;
      size.w = w;
      size.h = h;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    };
    applySize();
    const observer = new ResizeObserver(applySize);
    observer.observe(canvas);

    const cur = { ...STATE_PARAMS.idle };
    let simTime = 0;
    let phase = 0;
    let last = performance.now();
    let lastRender = 0;
    let raf = 0;

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (now - lastRender < 1000 / FPS - 1) return;
      lastRender = now;
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;

      const target = STATE_PARAMS[stateRef.current];
      const level = levelRef.current();
      cur.amp = approach(cur.amp, target.amp + target.gain * level, 6, dt);
      cur.bright = approach(cur.bright, target.bright, 4, dt);
      cur.gather = approach(cur.gather, target.gather, 4, dt);
      cur.rot = approach(cur.rot, target.rot, 3, dt);
      cur.speed = approach(cur.speed, target.speed, 3, dt);
      simTime += dt * (0.4 + 0.6 * cur.speed);
      phase += cur.rot * dt; // thinking: rows scroll — reads as a swirl

      const W = canvas.width;
      const H = canvas.height;
      const cx = W / 2;
      // Orb sits a touch high so its floor glow fits inside the canvas —
      // mirrors the WebGL camera framing.
      const cy = H * 0.42;
      const R = Math.min(W, H) * 0.38;
      const breathe = 1 + 0.015 * Math.sin(simTime * 0.7);
      const rr = R * breathe;

      ctx.clearRect(0, 0, W, H); // transparent: the orb floats on the app bg

      // Floor glow.
      const fg = ctx.createRadialGradient(cx, cy + rr * 1.28, rr * 0.05, cx, cy + rr * 1.28, rr * 0.9);
      fg.addColorStop(0, `rgba(90,120,246,${0.16 * cur.bright})`);
      fg.addColorStop(1, "rgba(0,0,0,0)");
      ctx.save();
      ctx.translate(cx, cy + rr * 1.28);
      ctx.scale(1, 0.28);
      ctx.translate(-cx, -(cy + rr * 1.28));
      ctx.fillStyle = fg;
      ctx.fillRect(0, 0, W, H * 2);
      ctx.restore();

      // Waveform dots, clipped to the orb.
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, rr * 0.97, 0, Math.PI * 2);
      ctx.clip();
      ctx.globalCompositeOperation = "lighter";
      const gather = 1 - 0.22 * cur.gather;
      const dotR = Math.max(1, rr * 0.012);
      for (let row = 0; row < ROWS; row++) {
        const zn = ((row / (ROWS - 1)) * 2 - 1) * 0.62 * gather;
        const rowPhase = phase * 2 + row * 0.9;
        for (let col = 0; col < COLS; col++) {
          const xn = ((col / (COLS - 1)) * 2 - 1) * gather;
          const rn = Math.hypot(xn, zn);
          if (rn > 0.95) continue;
          const dome = Math.sqrt(Math.max(0.9 - rn * rn, 0));
          const w =
            Math.sin(xn * 4.2 + simTime * 1.9 + rowPhase) * 0.55 +
            Math.sin(xn * 7.7 - simTime * 1.3 + zn * 3.0) * 0.3 +
            Math.sin(xn * 2.1 + simTime * 0.7 + row) * 0.35;
          let yn = cur.amp * w * (0.25 + 0.75 * dome);
          yn = Math.max(-dome, Math.min(dome, yn)) - 0.1 * cur.gather;
          const x = cx + xn * rr;
          const y = cy + (zn * 0.55 + yn) * rr;
          const a =
            Math.min(1, (0.95 - rn) * 6) *
            (0.32 + 0.5 * Math.min(1, Math.abs(w))) *
            Math.min(1, cur.bright);
          ctx.fillStyle = `${LUT[col]}${a.toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(x, y, dotR, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();

      // Fresnel-ish rim: gradient stroke + a soft outer echo.
      const rim = ctx.createLinearGradient(cx - rr, cy, cx + rr, cy);
      rim.addColorStop(0, PALETTE.cyan);
      rim.addColorStop(0.55, PALETTE.blue);
      rim.addColorStop(1, PALETTE.purple);
      ctx.globalCompositeOperation = "lighter";
      for (const [width, alpha] of [
        [1.6, 0.75],
        [4.5, 0.16],
        [8, 0.06],
      ] as const) {
        ctx.beginPath();
        ctx.arc(cx, cy, rr, 0, Math.PI * 2);
        ctx.strokeStyle = rim;
        ctx.lineWidth = width * dpr;
        ctx.globalAlpha = alpha * cur.bright;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    };
    raf = requestAnimationFrame(frame);

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(raf);
      } else {
        last = performance.now();
        lastRender = 0;
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={canvasRef} aria-hidden="true" className="block h-full w-full" />;
}

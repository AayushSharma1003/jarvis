// The signature visual: a glass orb with a cyan→purple audio-reactive particle
// waveform inside (docs/design/sphere.md; refs in docs/design/sphere-refs/).
//
// Vanilla three.js on purpose: it's one component, and the render loop reads
// audio through a getter at frame rate — React never re-renders for motion.
//
// Perf decisions (the 8 GB M2 is the smoothness bar; smoothness > fidelity):
// - ~6k points displaced in the VERTEX shader; the CPU never touches vertices.
// - The "glass" is a Fresnel rim shell, NOT MeshPhysicalMaterial transmission.
//   Real transmission re-renders the scene for refraction nobody can see
//   against a near-black background; the reference gif's glass reads as rim
//   glow + interior particles, which this is.
// - Bloom at half resolution, DPR capped at 1.5, 30 fps idle / 60 fps active,
//   rendering pauses while the window is hidden.
// - A frame-time watchdog + WebGL-context-lost handler call onPerfProblem();
//   the parent swaps in the mandatory canvas-2D fallback.

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { approach, PALETTE, STATE_PARAMS, type SphereVisualState } from "./params";

interface SphereProps {
  visualState: SphereVisualState;
  getLevel: () => number;
  /** Idle renders at 30 fps; active (voice states) at 60. */
  onPerfProblem: () => void;
}

const MAX_DPR = 1.5;
const APP_BG = "#18181b"; // Tailwind zinc-900 — MUST match ChatView's bg
const RIBBON_ROWS = 44;
const RIBBON_COLS = 168;
const DISC_R = 0.92; // waveform stays inside the r=1 shell
const SPARKLE_COUNT = 140;
// Watchdog: judge the GPU by how long our own render call blocks the CPU —
// never by frame cadence, which OS/webview rAF throttling (occluded windows,
// battery saver) pollutes into false fallbacks. Software rasterizers (the
// flaky-WebGL case the 2D fallback exists for) blow this budget instantly.
const WATCHDOG_RENDER_MS = 12;
const WATCHDOG_TRIP_S = 4;
const WATCHDOG_WARMUP_S = 3; // first frames compile shaders; don't count them

// -- GLSL --------------------------------------------------------------------

// Ashima/Gustavson 3D simplex noise (MIT), the standard GLSL implementation.
const SNOISE = /* glsl */ `
vec3 mod289(vec3 x){return x - floor(x * (1.0/289.0)) * 289.0;}
vec4 mod289(vec4 x){return x - floor(x * (1.0/289.0)) * 289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159 - 0.85373472095314 * r;}
float snoise(vec3 v){
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
      i.z + vec4(0.0, i1.z, i2.z, 1.0))
    + i.y + vec4(0.0, i1.y, i2.y, 1.0))
    + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}
`;

const GRADIENT = /* glsl */ `
uniform vec3 uCyan;
uniform vec3 uBlue;
uniform vec3 uPurple;
vec3 gradient(float t) {
  return t < 0.5 ? mix(uCyan, uBlue, t * 2.0) : mix(uBlue, uPurple, t * 2.0 - 1.0);
}
`;

const RIBBON_VERT = /* glsl */ `
uniform float uTime, uSpeed, uAmp, uGather, uBright, uSize;
attribute float aEdge;
varying vec3 vColor;
varying float vAlpha;
${GRADIENT}
${SNOISE}
const float R = ${DISC_R.toFixed(3)};
void main() {
  vec3 p = position;
  p.xz *= 1.0 - 0.22 * uGather;
  float r = length(p.xz);
  float t = uTime * uSpeed;
  float n1 = snoise(vec3(p.x * 1.5, p.z * 1.7, t * 0.35));
  float n2 = snoise(vec3(p.x * 3.3 + 11.0, p.z * 3.1, t * 0.55));
  float dome = sqrt(max(R * R - r * r, 0.0));
  float y = uAmp * (0.72 * n1 + 0.28 * n2) * (0.25 + 0.75 * dome);
  y = clamp(y, -dome * 0.95, dome * 0.95) + 0.1 * uGather;
  p.y = y;

  float h = clamp(abs(y) / max(uAmp, 0.001), 0.0, 1.0);
  vColor = gradient(position.x * 0.5 + 0.5) * uBright * (0.5 + 0.6 * h);
  vAlpha = aEdge;

  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = uSize * (1.0 + 0.6 * h) / max(-mv.z, 0.1);
  gl_Position = projectionMatrix * mv;
}
`;

const POINT_FRAG = /* glsl */ `
precision mediump float;
varying vec3 vColor;
varying float vAlpha;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.12, d);
  gl_FragColor = vec4(vColor, a * vAlpha);
}
`;

const SPARKLE_VERT = /* glsl */ `
uniform float uTime, uBright, uSize;
attribute float aSeed;
varying vec3 vColor;
varying float vAlpha;
${GRADIENT}
void main() {
  vec3 p = position;
  p.y += 0.05 * sin(uTime * 0.4 + aSeed * 6.283);
  p.x += 0.03 * sin(uTime * 0.27 + aSeed * 12.566);
  vColor = gradient(aSeed) * uBright * 0.6;
  vAlpha = 0.35 + 0.3 * sin(uTime * 1.7 + aSeed * 20.0);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = uSize / max(-mv.z, 0.1);
  gl_Position = projectionMatrix * mv;
}
`;

const SHELL_VERT = /* glsl */ `
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
void main() {
  vNormal = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vView = -mv.xyz;
  vPos = position;
  gl_Position = projectionMatrix * mv;
}
`;

const SHELL_FRAG = /* glsl */ `
precision mediump float;
uniform float uBright;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vPos;
${GRADIENT}
void main() {
  float fresnel = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 2.5);
  vec3 c = gradient(clamp(vPos.x * 0.45 + 0.5 + 0.2 * vPos.y, 0.0, 1.0));
  gl_FragColor = vec4(c * uBright, fresnel * 0.7 + 0.012);
}
`;

// -- scene building ----------------------------------------------------------

function colorUniforms() {
  return {
    uCyan: { value: new THREE.Color(PALETTE.cyan) },
    uBlue: { value: new THREE.Color(PALETTE.blue) },
    uPurple: { value: new THREE.Color(PALETTE.purple) },
  };
}

function buildRibbon(dpr: number): THREE.Points {
  const positions: number[] = [];
  const edges: number[] = [];
  for (let row = 0; row < RIBBON_ROWS; row++) {
    const z = (row / (RIBBON_ROWS - 1)) * 2 - 1; // -1..1, scaled below
    for (let col = 0; col < RIBBON_COLS; col++) {
      const x = ((col / (RIBBON_COLS - 1)) * 2 - 1) * DISC_R;
      const zz = z * DISC_R * 0.8;
      const r = Math.hypot(x, zz);
      if (r > DISC_R) continue;
      positions.push(x, 0, zz);
      edges.push(Math.min(1, (DISC_R - r) * 6)); // fade the outermost dots
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("aEdge", new THREE.Float32BufferAttribute(edges, 1));
  const mat = new THREE.ShaderMaterial({
    vertexShader: RIBBON_VERT,
    fragmentShader: POINT_FRAG,
    uniforms: {
      ...colorUniforms(),
      uTime: { value: 0 },
      uSpeed: { value: 0.5 },
      uAmp: { value: 0.16 },
      uGather: { value: 0 },
      uBright: { value: 0.6 },
      uSize: { value: 4.8 * dpr },
    },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false; // shader-displaced; bounds are meaningless
  return points;
}

function buildSparkles(dpr: number): THREE.Points {
  const positions: number[] = [];
  const seeds: number[] = [];
  for (let i = 0; i < SPARKLE_COUNT; i++) {
    // Uniform-ish inside the sphere: rejection sample.
    let x = 0,
      y = 0,
      z = 0;
    do {
      x = Math.random() * 2 - 1;
      y = Math.random() * 2 - 1;
      z = Math.random() * 2 - 1;
    } while (x * x + y * y + z * z > 1);
    positions.push(x * 0.85, y * 0.85, z * 0.85);
    seeds.push(Math.random());
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("aSeed", new THREE.Float32BufferAttribute(seeds, 1));
  const mat = new THREE.ShaderMaterial({
    vertexShader: SPARKLE_VERT,
    fragmentShader: POINT_FRAG,
    uniforms: {
      ...colorUniforms(),
      uTime: { value: 0 },
      uBright: { value: 0.6 },
      uSize: { value: 2.5 * dpr },
    },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false;
  return points;
}

function buildShell(): THREE.Mesh {
  const geo = new THREE.SphereGeometry(1, 48, 48);
  const mat = new THREE.ShaderMaterial({
    vertexShader: SHELL_VERT,
    fragmentShader: SHELL_FRAG,
    uniforms: { ...colorUniforms(), uBright: { value: 0.6 } },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  return new THREE.Mesh(geo, mat);
}

/** Soft blue floor glow: a radial-gradient ellipse texture, not a mirror pass. */
function buildFloor(): { mesh: THREE.Mesh; texture: THREE.CanvasTexture } {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 128;
  const ctx = c.getContext("2d")!;
  const glow = ctx.createRadialGradient(128, 64, 2, 128, 64, 126);
  glow.addColorStop(0, "rgba(96,130,246,0.55)");
  glow.addColorStop(0.4, "rgba(96,110,220,0.18)");
  glow.addColorStop(1, "rgba(96,110,220,0)");
  ctx.save();
  ctx.translate(0, 64);
  ctx.scale(1, 0.5); // squash the radial falloff into an ellipse
  ctx.translate(0, -64);
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, 256, 128);
  ctx.restore();
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace; // canvas pixels are sRGB
  const mat = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    opacity: 0.3,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 0.7), mat);
  mesh.position.y = -1.24;
  return { mesh, texture };
}

/** The navy stage from the design, as a vignette that dissolves into APP_BG. */
function buildBackdrop(): { mesh: THREE.Mesh; texture: THREE.CanvasTexture } {
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 128;
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = APP_BG;
  ctx.fillRect(0, 0, 128, 128);
  const g = ctx.createRadialGradient(64, 64, 4, 64, 64, 64);
  g.addColorStop(0, PALETTE.bg);
  g.addColorStop(0.75, "rgba(13,13,22,0.4)");
  g.addColorStop(1, "rgba(13,13,22,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace; // canvas pixels are sRGB
  const mat = new THREE.MeshBasicMaterial({ map: texture, depthWrite: false });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(7, 7), mat);
  mesh.position.z = -1.6;
  return { mesh, texture };
}

// -- component ---------------------------------------------------------------

export function Sphere({ visualState, getLevel, onPerfProblem }: SphereProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef(visualState);
  stateRef.current = visualState;
  const levelRef = useRef(getLevel);
  levelRef.current = getLevel;
  const perfRef = useRef(onPerfProblem);
  perfRef.current = onPerfProblem;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: false, // bloom already softens; MSAA is wasted here
        // Opaque on purpose: UnrealBloom's composite writes alpha=1, so a
        // "transparent" canvas turns into a dark square anyway. Instead we
        // clear to the app's exact background and paint the navy stage as an
        // in-scene vignette — edges dissolve, orb floats.
        alpha: false,
        powerPreference: "low-power", // E-core/integrated is plenty (8 GB M2)
      });
    } catch {
      perfRef.current();
      return;
    }
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    renderer.setPixelRatio(dpr);
    renderer.setClearColor(new THREE.Color(APP_BG), 1);
    host.appendChild(renderer.domElement);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.display = "block";
    renderer.domElement.setAttribute("aria-hidden", "true");

    const scene = new THREE.Scene();
    // Pulled back far enough that the floor glow under the orb is in frame.
    const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 10);
    camera.position.set(0, 0.18, 3.85);
    camera.lookAt(0, -0.12, 0);

    const group = new THREE.Group();
    const ribbon = buildRibbon(dpr);
    const sparkles = buildSparkles(dpr);
    const shell = buildShell();
    group.add(ribbon, sparkles, shell);
    const backdrop = buildBackdrop();
    scene.add(backdrop.mesh);
    scene.add(group);
    const floor = buildFloor();
    scene.add(floor.mesh);

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    // Threshold keeps the dim interior crisp; only the bright crest blooms.
    const bloom = new UnrealBloomPass(new THREE.Vector2(128, 128), 0.55, 0.4, 0.32);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());

    const size = { w: 0, h: 0 };
    const applySize = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (w === 0 || h === 0 || (w === size.w && h === size.h)) return;
      size.w = w;
      size.h = h;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
      composer.setSize(w, h);
      // Half-resolution bloom: the mip blur hides it completely. At docked
      // size the blur would swallow the whole orb — skip the pass entirely.
      bloom.enabled = w >= 100;
      bloom.setSize(Math.max(64, (w * dpr) / 2), Math.max(64, (h * dpr) / 2));
    };
    applySize();
    // Debounced: during the dock⇄center CSS transition the canvas stretches
    // visually; render targets only re-allocate once the size settles.
    let resizeTimer: number | undefined;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(applySize, 180);
    });
    observer.observe(host);

    const ribbonU = (ribbon.material as THREE.ShaderMaterial).uniforms;
    const sparkleU = (sparkles.material as THREE.ShaderMaterial).uniforms;
    const shellU = (shell.material as THREE.ShaderMaterial).uniforms;
    const floorMat = floor.mesh.material as THREE.MeshBasicMaterial;

    // Live params, eased toward the state targets each frame.
    const cur = { ...STATE_PARAMS.idle };
    let simTime = 0;
    let last = performance.now();
    let lastRender = 0;
    let elapsed = 0;
    let emaRenderMs = 0;
    let badTime = 0;
    let raf = 0;
    let disposed = false;

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const active = stateRef.current !== "idle";
      const targetInterval = active ? 1000 / 60 : 1000 / 30;
      if (now - lastRender < targetInterval - 1) return;
      lastRender = now;
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      elapsed += dt;

      const target = STATE_PARAMS[stateRef.current];
      const level = levelRef.current();
      cur.amp = approach(cur.amp, target.amp + target.gain * level, 6, dt);
      cur.bright = approach(cur.bright, target.bright, 4, dt);
      cur.gather = approach(cur.gather, target.gather, 4, dt);
      cur.rot = approach(cur.rot, target.rot, 3, dt);
      cur.speed = approach(cur.speed, target.speed, 3, dt);
      simTime += dt * (0.4 + 0.6 * cur.speed);

      group.rotation.y += cur.rot * dt;
      const breathe = 1 + 0.015 * Math.sin(simTime * 0.7);
      group.scale.setScalar(breathe);

      // Docked-size compensation: fixed-pixel additive points would overlap
      // and saturate to white in a 32 px canvas — shrink and dim with size.
      const sizeScale = Math.min(1.4, Math.max(0.15, size.h / 240));
      const bright = cur.bright * (0.55 + 0.45 * Math.min(1, size.h / 140));

      ribbonU.uTime.value = simTime;
      ribbonU.uSpeed.value = cur.speed;
      ribbonU.uAmp.value = cur.amp;
      ribbonU.uGather.value = cur.gather;
      ribbonU.uBright.value = bright;
      ribbonU.uSize.value = 4.8 * dpr * sizeScale;
      sparkleU.uTime.value = simTime;
      sparkleU.uBright.value = bright;
      sparkleU.uSize.value = 2.5 * dpr * sizeScale;
      shellU.uBright.value = bright;
      floorMat.opacity = 0.18 + 0.22 * bright;
      bloom.strength = 0.4 + 0.35 * bright;

      const renderStart = performance.now();
      composer.render();
      emaRenderMs = emaRenderMs * 0.95 + (performance.now() - renderStart) * 0.05;
      if (import.meta.env.DEV) host.dataset.renderMs = emaRenderMs.toFixed(2);
      if (elapsed > WATCHDOG_WARMUP_S && emaRenderMs > WATCHDOG_RENDER_MS) {
        badTime += dt;
        if (badTime > WATCHDOG_TRIP_S) perfRef.current();
      } else {
        badTime = Math.max(0, badTime - dt * 0.5);
      }
    };
    raf = requestAnimationFrame(frame);

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(raf);
      } else if (!disposed) {
        last = performance.now();
        lastRender = 0;
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    const onContextLost = (e: Event) => {
      e.preventDefault();
      perfRef.current();
    };
    renderer.domElement.addEventListener("webglcontextlost", onContextLost);

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      window.clearTimeout(resizeTimer);
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost);
      for (const obj of [ribbon, sparkles, shell]) {
        obj.geometry.dispose();
        (obj.material as THREE.Material).dispose();
      }
      for (const flat of [floor, backdrop]) {
        flat.mesh.geometry.dispose();
        (flat.mesh.material as THREE.Material).dispose();
        flat.texture.dispose();
      }
      composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  return <div ref={hostRef} className="h-full w-full" />;
}

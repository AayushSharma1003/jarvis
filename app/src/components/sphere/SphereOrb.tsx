// The orb, placed. Adaptive (the product decision): a small orb docked at the
// top-right while you chat; it glides to center stage whenever the voice loop
// is active (or the conversation is empty — the hero moment). One canvas the
// whole time: the container's position/size transitions in CSS, and the
// renderer re-fits after the move settles.
//
// This component also owns renderer selection: WebGL when a probe context
// succeeds and the machine hasn't previously tripped the frame-time watchdog
// (persisted — a GPU that stuttered once will stutter tomorrow), else the
// mandatory canvas-2D fallback.

import { useCallback, useState } from "react";
import { useConversation } from "../../state/conversation";
import { visualStateOf } from "./params";
import { Sphere } from "./Sphere";
import { SphereFallback2D } from "./SphereFallback2D";
import { useAudioLevels } from "./useAudioLevels";

const FALLBACK_KEY = "jarvis.sphere.fallback";

function webglAvailable(): boolean {
  try {
    const probe = document.createElement("canvas");
    const gl = probe.getContext("webgl2") ?? probe.getContext("webgl");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

export function SphereOrb() {
  const voiceState = useConversation((s) => s.voiceState);
  const hasContent = useConversation(
    (s) => s.messages.length > 0 || s.streamingText !== null,
  );
  const getLevel = useAudioLevels();

  const [mode, setMode] = useState<"webgl" | "2d">(() =>
    localStorage.getItem(FALLBACK_KEY) === "1" || !webglAvailable() ? "2d" : "webgl",
  );
  const onPerfProblem = useCallback(() => {
    localStorage.setItem(FALLBACK_KEY, "1");
    setMode("2d");
  }, []);

  const visual = visualStateOf(voiceState);
  const centered = visual !== "idle" || !hasContent;

  // Keep 60 fps easing headroom on scroll-heavy chats: while docked the orb
  // still breathes, but at 30 fps and 30 px it costs nothing measurable.
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute z-10 overflow-hidden rounded-full transition-all duration-500 ease-out"
      style={
        centered
          ? { left: "calc(50% - 120px)", top: 52, width: 240, height: 240 }
          : // Docked: the header's empty center — never collides with
            // right-aligned bubbles, and the move is a straight glide up.
            { left: "calc(50% - 16px)", top: 4, width: 32, height: 32 }
      }
    >
      {mode === "webgl" ? (
        <Sphere visualState={visual} getLevel={getLevel} onPerfProblem={onPerfProblem} />
      ) : (
        <SphereFallback2D visualState={visual} getLevel={getLevel} />
      )}
    </div>
  );
}

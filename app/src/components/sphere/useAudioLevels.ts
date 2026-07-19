// The sphere's audio feed. voice.level arrives over the WS at 10 Hz; the orb
// renders at 30–60 fps. This hook subscribes to the store OUTSIDE React (no
// re-renders at audio rate) and hands back a per-frame getter that smooths
// the sparse updates — fast attack so speech onsets hit, slow decay so the
// waveform falls instead of snapping.

import { useCallback, useEffect, useRef } from "react";
import { useConversation } from "../../state/conversation";

const ATTACK_RATE = 22; // per-second approach when the target is louder
const DECAY_RATE = 7; // and when it's quieter

export function useAudioLevels(): () => number {
  const ref = useRef({ target: 0, value: 0, last: performance.now() });

  useEffect(() => {
    ref.current.target = useConversation.getState().voiceLevel;
    return useConversation.subscribe((s) => {
      ref.current.target = s.voiceLevel;
    });
  }, []);

  return useCallback(() => {
    const st = ref.current;
    const now = performance.now();
    const dt = Math.min((now - st.last) / 1000, 0.1);
    st.last = now;
    const rate = st.target > st.value ? ATTACK_RATE : DECAY_RATE;
    st.value += (st.target - st.value) * Math.min(1, rate * dt);
    return st.value;
  }, []);
}

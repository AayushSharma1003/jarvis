import { useTranslation } from "react-i18next";

// `‹ 2 / 3 ›` — moving between the alternatives for one turn (M5.5).
//
// A *turn* is the branching unit (user message + the whole assistant span), so
// there is one switcher per turn rather than one per message, and it renders
// under the user bubble because that is where the fork happened. Regenerating
// an answer moves the same counter: structurally it is the same relationship,
// even though ChatGPT floats its counter under whichever message differs.
//
// `siblings` always contains this turn, so "is there anything to switch to?" is
// a plain `length > 1` and the arrows are ordinary array neighbours.

export function BranchSwitcher({
  siblings,
  current,
  onSwitch,
}: {
  siblings: string[];
  current: string;
  onSwitch: (turnId: string) => void;
}) {
  const { t } = useTranslation();
  const index = siblings.indexOf(current);
  if (siblings.length < 2 || index === -1) return null;

  const go = (delta: number) => onSwitch(siblings[index + delta]);

  return (
    <div className="mt-1 flex items-center justify-end gap-1 text-xs text-zinc-500">
      <button
        type="button"
        onClick={() => go(-1)}
        disabled={index === 0}
        aria-label={t("chat.branchPrev")}
        className="rounded p-0.5 hover:bg-zinc-800 hover:text-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-600 disabled:pointer-events-none disabled:opacity-30"
      >
        <Chevron direction="left" />
      </button>
      <span className="tabular-nums" aria-live="polite">
        {t("chat.branchOf", { index: index + 1, count: siblings.length })}
      </span>
      <button
        type="button"
        onClick={() => go(1)}
        disabled={index === siblings.length - 1}
        aria-label={t("chat.branchNext")}
        className="rounded p-0.5 hover:bg-zinc-800 hover:text-zinc-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-600 disabled:pointer-events-none disabled:opacity-30"
      >
        <Chevron direction="right" />
      </button>
    </div>
  );
}

function Chevron({ direction }: { direction: "left" | "right" }) {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor">
      <path
        d={direction === "left" ? "M12 5l-5 5 5 5" : "M8 5l5 5-5 5"}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

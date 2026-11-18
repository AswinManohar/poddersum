"use client";

import type { Claim } from "@/lib/models";
import { AnswerFooter, ClaimList } from "./Claims";
import { useArtifact } from "./hooks";
import { ProblemState, TextSkeleton } from "./ProblemState";

const SECTIONS: { id: string; title: string }[] = [
  { id: "who_its_for", title: "Who it is for" },
  { id: "whats_new", title: "What is new" },
  { id: "best_10_minutes", title: "Best 10 minutes" },
];
const OTHER = { id: "other", title: "Also worth knowing" };

/** Group verdict claims by `section`, in display order; unknown or missing sections last. */
export function groupVerdict(claims: Claim[]): { id: string; title: string; claims: Claim[] }[] {
  const known = new Set(SECTIONS.map((s) => s.id));
  const groups = SECTIONS.map((s) => ({ ...s, claims: claims.filter((c) => c.section === s.id) }));
  const rest = claims.filter((c) => !c.section || !known.has(c.section));
  return [...groups, { ...OTHER, claims: rest }].filter((g) => g.claims.length > 0);
}

/** "Is this worth my time?" - loaded automatically, shown before any summary. */
export function VerdictPanel({ episodeId, audioUrl }: { episodeId: string; audioUrl: string }) {
  const { state, retry } = useArtifact(episodeId, "verdict", true);

  return (
    <section
      aria-labelledby="verdict-heading"
      aria-busy={state.kind === "loading"}
      className="rounded-xl border border-line bg-surface p-5"
      data-testid="verdict-panel"
    >
      <h2 id="verdict-heading" className="font-display text-xl font-semibold">
        Is this worth my time?
      </h2>
      <div className="mt-4" aria-live="polite">
        {state.kind === "loading" || state.kind === "idle" ? (
          <TextSkeleton lines={3} label="Weighing up this episode. The first time can take a few seconds." />
        ) : state.kind === "error" ? (
          <ProblemState problem={state.problem} onRetry={retry} />
        ) : state.answer.claims.length === 0 ? (
          <p className="text-ink-muted">No verdict could be drawn from this transcript.</p>
        ) : (
          <div className="space-y-5">
            {groupVerdict(state.answer.claims).map((group) => (
              <div key={group.id} className="space-y-2">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">{group.title}</h3>
                <ClaimList claims={group.claims} audioUrl={audioUrl} />
              </div>
            ))}
            <AnswerFooter answer={state.answer} />
          </div>
        )}
      </div>
    </section>
  );
}

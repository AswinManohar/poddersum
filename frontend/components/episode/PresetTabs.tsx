"use client";

import { useId, useRef, useState, type KeyboardEvent } from "react";
import type { ArtifactKind } from "@/lib/models";
import { AnswerFooter, ClaimList } from "./Claims";
import { useArtifact } from "./hooks";
import { ProblemState, TextSkeleton } from "./ProblemState";

type PresetKind = Extract<ArtifactKind, "tldr" | "takeaways">;

const TABS: { kind: PresetKind; label: string; loading: string }[] = [
  { kind: "tldr", label: "TL;DR", loading: "Writing the TL;DR" },
  { kind: "takeaways", label: "Takeaways", loading: "Collecting the takeaways" },
];

function PresetPanel({
  episodeId,
  kind,
  loadingLabel,
  opened,
  audioUrl,
}: {
  episodeId: string;
  kind: PresetKind;
  loadingLabel: string;
  opened: boolean;
  audioUrl: string;
}) {
  const { state, retry } = useArtifact(episodeId, kind, opened);
  if (state.kind === "idle" || state.kind === "loading") return <TextSkeleton lines={4} label={loadingLabel} />;
  if (state.kind === "error") return <ProblemState problem={state.problem} onRetry={retry} />;
  if (state.answer.claims.length === 0) {
    return <p className="text-ink-muted">Nothing to show for this episode.</p>;
  }
  return (
    <div className="space-y-4">
      <ClaimList claims={state.answer.claims} audioUrl={audioUrl} />
      <AnswerFooter answer={state.answer} />
    </div>
  );
}

/**
 * TL;DR and Takeaways. Nothing is fetched until a tab is opened; each panel stays mounted
 * once opened, so switching back is instant. Keyboard: arrows, Home and End move between
 * tabs (automatic activation).
 */
export function PresetTabs({ episodeId, audioUrl }: { episodeId: string; audioUrl: string }) {
  const baseId = useId();
  const [selected, setSelected] = useState<PresetKind | null>(null);
  const [opened, setOpened] = useState<ReadonlySet<PresetKind>>(new Set());
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function open(kind: PresetKind) {
    setSelected(kind);
    setOpened((prev) => (prev.has(kind) ? prev : new Set(prev).add(kind)));
  }

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number | null = null;
    if (e.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    if (next === null) return;
    e.preventDefault();
    tabRefs.current[next]?.focus();
    open(TABS[next].kind);
  }

  const focusable = selected ?? TABS[0].kind;

  return (
    <section aria-labelledby={`${baseId}-heading`} className="rounded-xl border border-line bg-surface p-5">
      <h2 id={`${baseId}-heading`} className="font-display text-xl font-semibold">
        Summaries
      </h2>
      <div role="tablist" aria-label="Summaries" className="mt-3 flex gap-2 border-b border-line">
        {TABS.map((tab, i) => {
          const isSelected = selected === tab.kind;
          return (
            <button
              key={tab.kind}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.kind}`}
              aria-selected={isSelected}
              aria-controls={`${baseId}-panel-${tab.kind}`}
              tabIndex={focusable === tab.kind ? 0 : -1}
              onClick={() => open(tab.kind)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={`-mb-px border-b-2 px-3 py-2 font-medium ${
                isSelected ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {selected === null ? (
        <p className="mt-4 text-sm text-ink-muted">Pick a summary above. Each is written once, then saved.</p>
      ) : null}
      {TABS.map((tab) => (
        <div
          key={tab.kind}
          role="tabpanel"
          id={`${baseId}-panel-${tab.kind}`}
          aria-labelledby={`${baseId}-tab-${tab.kind}`}
          hidden={selected !== tab.kind}
          tabIndex={0}
          aria-live="polite"
          className="mt-4"
        >
          {opened.has(tab.kind) ? (
            <PresetPanel
              episodeId={episodeId}
              kind={tab.kind}
              loadingLabel={tab.loading}
              opened
              audioUrl={audioUrl}
            />
          ) : null}
        </div>
      ))}
    </section>
  );
}

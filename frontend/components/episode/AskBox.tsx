"use client";

import { useRef, useState } from "react";
import { askPath } from "@/lib/api";
import { AnswerFooter, ClaimList } from "./Claims";
import { Composer } from "./Composer";
import { useAnswerStream } from "./hooks";
import { ProblemState, TextSkeleton } from "./ProblemState";

export const ASK_ANGLES: { label: string; text: string }[] = [
  { label: "Summarize for a startup founder", text: "Summarize for a startup founder" },
  { label: "Only the parts about ...", text: "Only the parts about " },
  { label: "Explain it to a beginner", text: "Explain the main ideas to a beginner" },
  { label: "Where do they disagree?", text: "Where do the speakers disagree, and why?" },
];

/** Ask anything: any angle, streamed claim by claim, every claim cited. */
export function AskBox({ episodeId, audioUrl }: { episodeId: string; audioUrl: string }) {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const { state, run, cancel } = useAnswerStream();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const busy = state.phase === "streaming";

  function submit(text = question) {
    const q = text.trim();
    if (!q) return;
    setAsked(q);
    void run(askPath(episodeId), { question: q });
  }

  function prefill(text: string) {
    setQuestion(text);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(text.length, text.length);
    });
  }

  const streamedClaims = state.phase === "streaming" ? state.claims.filter(Boolean) : [];

  return (
    <section aria-labelledby="ask-heading" className="rounded-xl border border-line bg-surface p-5">
      <h2 id="ask-heading" className="font-display text-xl font-semibold">
        Ask anything
      </h2>
      <p className="mt-1 text-sm text-ink-muted">Any angle. Every answer links back to the moment it came from.</p>
      <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Example angles">
        {ASK_ANGLES.map((a) => (
          <button
            key={a.label}
            type="button"
            onClick={() => prefill(a.text)}
            className="rounded-full border border-line bg-surface-muted px-3 py-1 text-sm text-ink hover:border-accent"
          >
            {a.label}
          </button>
        ))}
      </div>
      <div className="mt-4">
        <Composer
          ref={textareaRef}
          label="Your question"
          value={question}
          onChange={setQuestion}
          onSubmit={() => submit()}
          busy={busy}
          submitLabel="Ask"
          onCancel={cancel}
          placeholder="e.g. What should a product manager take from this?"
        />
      </div>

      <div aria-live="polite" aria-busy={busy} className="mt-5 empty:hidden" data-testid="ask-answer">
        {state.phase === "idle" ? null : (
          <div className="space-y-3 border-t border-line pt-4">
            {asked ? (
              <p className="text-sm text-ink-muted">
                <span className="font-medium text-ink">You asked:</span> {asked}
              </p>
            ) : null}
            {state.phase === "streaming" ? (
              <>
                {streamedClaims.length > 0 ? <ClaimList claims={streamedClaims} audioUrl={audioUrl} /> : null}
                {streamedClaims.length === 0 ? (
                  <TextSkeleton lines={2} label={state.status ?? "Reading the transcript"} />
                ) : null}
                <p className="text-xs text-ink-muted" aria-hidden={streamedClaims.length === 0}>
                  {state.status ?? "Reading the transcript..."}
                </p>
              </>
            ) : state.phase === "error" ? (
              <ProblemState problem={state.problem} onRetry={() => submit(asked)} />
            ) : state.answer.claims.length === 0 ? (
              <p className="text-ink-muted">
                The transcript does not seem to cover that. Try another angle.
              </p>
            ) : (
              <>
                <ClaimList claims={state.answer.claims} audioUrl={audioUrl} />
                <AnswerFooter answer={state.answer} />
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

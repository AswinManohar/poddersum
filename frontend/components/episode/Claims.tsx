import { useId } from "react";
import { Badge } from "@/components/Badge";
import { audioLinkAt, formatCost, formatTimestamp } from "@/lib/format";
import type { Answer, Citation, Claim } from "@/lib/models";

/** A timestamp chip that opens the original audio at the cited moment in a new tab. */
export function CitationChip({ citation, audioUrl }: { citation: Citation; audioUrl: string }) {
  const tipId = useId();
  const time = formatTimestamp(citation.start_ms);
  const who = citation.speaker ? `, ${citation.speaker}` : "";
  const label = `Listen from ${time}${who} (opens the audio in a new tab)`;
  const title = citation.quote
    ? `${citation.speaker ? `${citation.speaker}: ` : ""}"${citation.quote}"`
    : `${citation.speaker ? `${citation.speaker} at ` : ""}${time}`;
  return (
    <span className="group relative inline-flex">
      <a
        href={audioLinkAt(audioUrl, citation.start_ms)}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={label}
        aria-describedby={citation.quote ? tipId : undefined}
        title={title}
        data-testid="citation"
        className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-muted px-2 py-0.5 font-mono text-xs font-medium text-accent hover:border-accent"
      >
        <span aria-hidden="true">&#9654;</span>
        {time}
      </a>
      {citation.quote ? (
        <span
          id={tipId}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-0 z-20 mb-1 hidden w-64 max-w-[80vw] rounded-lg border border-line bg-surface p-2 text-xs text-ink shadow-lg group-focus-within:block group-hover:block"
        >
          {citation.speaker ? <span className="font-semibold">{citation.speaker}: </span> : null}
          &ldquo;{citation.quote}&rdquo;
        </span>
      ) : null}
    </span>
  );
}

export function ClaimItem({ claim, audioUrl }: { claim: Claim; audioUrl: string }) {
  return (
    <li className="space-y-1.5" data-testid="claim">
      <p className="leading-relaxed">{claim.text}</p>
      <div className="flex flex-wrap gap-1.5" aria-label="Sources">
        {claim.citations.map((c, i) => (
          <CitationChip key={`${c.segment_id}-${i}`} citation={c} audioUrl={audioUrl} />
        ))}
      </div>
    </li>
  );
}

export function ClaimList({ claims, audioUrl }: { claims: Claim[]; audioUrl: string }) {
  return (
    <ul className="space-y-4">
      {claims.map((claim, i) => (
        <ClaimItem key={i} claim={claim} audioUrl={audioUrl} />
      ))}
    </ul>
  );
}

/** Model, cost and cache line under an answer. */
export function AnswerFooter({ answer }: { answer: Answer }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted" data-testid="answer-footer">
      <span>{answer.model}</span>
      <span aria-hidden="true">&middot;</span>
      <span>
        <span className="sr-only">Cost </span>
        {formatCost(answer.cost_usd)}
      </span>
      {answer.cached ? <Badge tone="ok">cached</Badge> : null}
    </p>
  );
}

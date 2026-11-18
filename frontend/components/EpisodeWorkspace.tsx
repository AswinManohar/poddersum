"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, startIngest } from "@/lib/api";
import { describeProblem, type Problem } from "@/lib/errors";
import type { EpisodeStatus } from "@/lib/models";
import { isAbortError, streamJobEvents } from "@/lib/sse";
import { AskBox } from "./episode/AskBox";
import { ChatPanel } from "./episode/ChatPanel";
import { JobFailed, JobLost, JobProgress, type JobSnapshot } from "./episode/JobProgress";
import { PresetTabs } from "./episode/PresetTabs";
import { VerdictPanel } from "./episode/VerdictPanel";

export interface EpisodeWorkspaceProps {
  episodeId: string;
  status: EpisodeStatus;
  activeJobId: string | null;
  audioUrl: string;
}

type Phase =
  | { kind: "ready" }
  | { kind: "working"; job: JobSnapshot | null }
  | { kind: "failed"; message: string | null }
  | { kind: "lost"; problem: Problem };

function initialPhase(status: EpisodeStatus): Phase {
  if (status === "ready") return { kind: "ready" };
  if (status === "failed") return { kind: "failed", message: null };
  return { kind: "working", job: null };
}

/**
 * Interactive area of the episode page.
 *
 * Not ready yet: `new` starts ingest at once; `processing` re-attaches to `activeJobId`
 * (the event stream replays from seq 1, so a refresh restores the progress; the
 * idempotent ingest POST is the fallback); `failed` offers a new attempt. When the job
 * succeeds the workspace switches to the ready layout in place and refreshes the server
 * components (status badge).
 *
 * Ready: the worth-it verdict first, then TL;DR / Takeaways tabs, the ask box and chat.
 */
export function EpisodeWorkspace({ episodeId, status, activeJobId, audioUrl }: EpisodeWorkspaceProps) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>(() => initialPhase(status));
  const [run, setRun] = useState(0);

  useEffect(() => {
    if (status === "ready") return;
    if (status === "failed" && run === 0) return;
    const controller = new AbortController();
    const { signal } = controller;
    const onEvent = (job: JobSnapshot) => setPhase({ kind: "working", job });

    async function follow(jobId: string) {
      const last = await streamJobEvents(jobId, { onEvent, signal });
      if (signal.aborted) return;
      if (last.state === "succeeded") {
        setPhase({ kind: "ready" });
        router.refresh();
      } else {
        setPhase({ kind: "failed", message: last.message ?? null });
      }
    }

    async function start() {
      const job = await startIngest(episodeId, { signal });
      if (signal.aborted) return;
      if (job.state === "succeeded") {
        setPhase({ kind: "ready" });
        router.refresh();
        return;
      }
      if (job.state === "failed") {
        setPhase({ kind: "failed", message: job.error ?? job.message ?? null });
        return;
      }
      onEvent(job);
      await follow(job.job_id);
    }

    (async () => {
      const resume = run === 0 && status === "processing" && activeJobId;
      if (!resume) return start();
      try {
        await follow(activeJobId);
      } catch (err) {
        // The job vanished (e.g. database reset): fall back to the idempotent POST.
        if (err instanceof ApiError && err.status === 404) return start();
        throw err;
      }
    })().catch((err: unknown) => {
      if (signal.aborted || isAbortError(err)) return;
      setPhase({ kind: "lost", problem: describeProblem(err) });
    });

    return () => controller.abort();
  }, [episodeId, status, activeJobId, run, router]);

  function retry() {
    setPhase({ kind: "working", job: null });
    setRun((r) => r + 1);
  }

  if (phase.kind === "ready") {
    return (
      <div className="space-y-6" data-testid="episode-workspace" data-status="ready">
        <VerdictPanel episodeId={episodeId} audioUrl={audioUrl} />
        <PresetTabs episodeId={episodeId} audioUrl={audioUrl} />
        <AskBox episodeId={episodeId} audioUrl={audioUrl} />
        <ChatPanel episodeId={episodeId} audioUrl={audioUrl} />
      </div>
    );
  }

  return (
    <section
      aria-labelledby="workspace-heading"
      data-testid="episode-workspace"
      data-status={phase.kind === "failed" ? "failed" : "processing"}
      className="rounded-xl border border-line bg-surface p-5"
    >
      <h2 id="workspace-heading" className="font-display text-xl font-semibold">
        Preparing this episode
      </h2>
      <p className="mt-1 text-sm text-ink-muted">
        PodderSum reads the transcript once; after that every summary and question is quick.
      </p>
      <div className="mt-4">
        {phase.kind === "working" ? (
          <JobProgress job={phase.job} />
        ) : phase.kind === "failed" ? (
          <JobFailed message={phase.message} onRetry={retry} />
        ) : (
          <JobLost problem={phase.problem} onRetry={retry} />
        )}
      </div>
    </section>
  );
}

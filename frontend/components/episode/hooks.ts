"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getArtifact } from "@/lib/api";
import { describeProblem, type Problem } from "@/lib/errors";
import type { Answer, AnswerStreamEvent, ArtifactKind, Claim } from "@/lib/models";
import { isAbortError, streamAnswer } from "@/lib/sse";

type ArtifactResult =
  | { key: string; kind: "ok"; answer: Answer }
  | { key: string; kind: "error"; problem: Problem };

export type ArtifactState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; answer: Answer }
  | { kind: "error"; problem: Problem };

/**
 * Fetch a preset answer once `enabled` is true, then keep it in memory for the life of the
 * component. `retry` refetches after an error.
 */
export function useArtifact(episodeId: string, kind: ArtifactKind, enabled: boolean) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<ArtifactResult | null>(null);
  const key = `${episodeId}:${kind}:${attempt}`;

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    getArtifact(episodeId, kind, { signal: controller.signal })
      .then((artifact) => setResult({ key, kind: "ok", answer: artifact.answer }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setResult({ key, kind: "error", problem: describeProblem(err) });
      });
    return () => controller.abort();
  }, [enabled, episodeId, kind, key]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  let state: ArtifactState;
  if (!enabled) state = { kind: "idle" };
  else if (result?.key !== key) state = { kind: "loading" };
  else state = result.kind === "ok" ? { kind: "ok", answer: result.answer } : { kind: "error", problem: result.problem };
  return { state, retry };
}

export type AnswerStreamState =
  | { phase: "idle" }
  | { phase: "streaming"; status: string | null; claims: Claim[] }
  | { phase: "done"; answer: Answer }
  | { phase: "error"; problem: Problem };

/**
 * Run one streamed answer at a time (ask or chat). Claims render as they arrive and are
 * replaced by the final `answer`. Starting a new run, `cancel` or unmount aborts the
 * current stream.
 */
export function useAnswerStream() {
  const [state, setState] = useState<AnswerStreamState>({ phase: "idle" });
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const run = useCallback(async (path: string, body: unknown): Promise<Answer | null> => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ phase: "streaming", status: null, claims: [] });

    const onEvent = (event: AnswerStreamEvent) => {
      if (controller.signal.aborted) return;
      if (event.event === "status") {
        setState((s) => (s.phase === "streaming" ? { ...s, status: event.message } : s));
      } else if (event.event === "claim") {
        setState((s) => {
          if (s.phase !== "streaming") return s;
          const claims = s.claims.slice();
          claims[event.index] = event.claim;
          return { ...s, claims };
        });
      }
    };

    try {
      const result = await streamAnswer(path, body, { onEvent, signal: controller.signal });
      if (controller.signal.aborted) return null;
      if (result.error) {
        setState({ phase: "error", problem: describeProblem(result.error) });
        return null;
      }
      if (result.answer) {
        setState({ phase: "done", answer: result.answer });
        return result.answer;
      }
      setState({ phase: "error", problem: describeProblem(new Error("The answer was empty.")) });
      return null;
    } catch (err) {
      if (isAbortError(err) || controller.signal.aborted) return null;
      setState({ phase: "error", problem: describeProblem(err) });
      return null;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setState({ phase: "idle" });
  }, []);

  const reset = useCallback(() => setState({ phase: "idle" }), []);

  return { state, run, cancel, reset };
}

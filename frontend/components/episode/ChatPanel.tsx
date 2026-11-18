"use client";

import { useCallback, useEffect, useState } from "react";
import { chatPath, getChatHistory } from "@/lib/api";
import { describeProblem, type Problem } from "@/lib/errors";
import type { Answer, ChatMessage } from "@/lib/models";
import { AnswerFooter, ClaimList } from "./Claims";
import { Composer } from "./Composer";
import { useAnswerStream } from "./hooks";
import { ProblemState, TextSkeleton } from "./ProblemState";

type HistoryResult =
  | { key: number; kind: "ok"; messages: ChatMessage[] }
  | { key: number; kind: "error"; problem: Problem };

interface LocalMessage {
  message: ChatMessage;
  /** Accounting for replies streamed in this session (history has none). */
  answer?: Answer;
}

function localId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `local-${Date.now()}-${Math.random()}`;
}

function Message({ item, audioUrl }: { item: LocalMessage; audioUrl: string }) {
  const { message } = item;
  if (message.role === "user") {
    return (
      <li className="flex justify-end" data-testid="chat-user">
        <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-xl rounded-br-sm bg-accent-soft px-3 py-2 text-ink">
          <span className="sr-only">You: </span>
          {message.text}
        </p>
      </li>
    );
  }
  return (
    <li className="space-y-2" data-testid="chat-assistant">
      <span className="sr-only">PodderSum: </span>
      {message.claims.length > 0 ? (
        <ClaimList claims={message.claims} audioUrl={audioUrl} />
      ) : (
        <p className="whitespace-pre-wrap break-words">{message.text}</p>
      )}
      {item.answer ? <AnswerFooter answer={item.answer} /> : null}
    </li>
  );
}

/** Follow-up chat about the episode. History reloads from the server on mount. */
export function ChatPanel({ episodeId, audioUrl }: { episodeId: string; audioUrl: string }) {
  const [attempt, setAttempt] = useState(0);
  const [history, setHistory] = useState<HistoryResult | null>(null);
  const [local, setLocal] = useState<LocalMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  const { state, run, cancel, reset } = useAnswerStream();
  const busy = state.phase === "streaming";

  useEffect(() => {
    const controller = new AbortController();
    getChatHistory(episodeId, { signal: controller.signal })
      .then((messages) => setHistory({ key: attempt, kind: "ok", messages }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setHistory({ key: attempt, kind: "error", problem: describeProblem(err) });
      });
    return () => controller.abort();
  }, [episodeId, attempt]);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message) return;
      const now = new Date().toISOString();
      const userMsg: ChatMessage = { id: localId(), role: "user", text: message, claims: [], created_at: now };
      setLocal((l) => [...l, { message: userMsg }]);
      setDraft("");
      setFailed(null);
      const answer = await run(chatPath(episodeId), { message });
      if (answer) {
        const reply: ChatMessage = {
          id: localId(),
          role: "assistant",
          text: answer.claims.map((c) => c.text).join(" "),
          claims: answer.claims,
          created_at: new Date().toISOString(),
        };
        setLocal((l) => [...l, { message: reply, answer }]);
        reset();
      } else {
        // Not answered: take the message back out so a retry does not show it twice.
        setLocal((l) => l.filter((m) => m.message.id !== userMsg.id));
        setFailed(message);
        setDraft((d) => (d === "" ? message : d));
      }
    },
    [episodeId, run, reset],
  );

  const current = history?.key === attempt ? history : null;
  const items: LocalMessage[] = [
    ...(current?.kind === "ok" ? current.messages.map((m) => ({ message: m })) : []),
    ...local,
  ];
  const streamedClaims = state.phase === "streaming" ? state.claims.filter(Boolean) : [];
  const historyUnavailable = current?.kind === "error";
  // 501 from either chat route: the feature is not deployed yet. Show one calm notice
  // instead of an error plus a composer that cannot work.
  const chatUnavailable =
    (current?.kind === "error" && current.problem.kind === "not_available") ||
    (state.phase === "error" && state.problem.kind === "not_available");

  if (chatUnavailable) {
    return (
      <section aria-labelledby="chat-heading" className="rounded-xl border border-line bg-surface p-5">
        <h2 id="chat-heading" className="font-display text-xl font-semibold">
          Chat about this episode
        </h2>
        <div role="status" data-testid="chat-unavailable" className="mt-4 rounded-lg border border-line bg-surface-muted p-4">
          <p className="font-semibold text-ink">Chat is not available yet</p>
          <p className="mt-1 text-sm text-ink-muted">
            Follow-up chat is coming soon. Until then, use Ask anything above; every answer is cited.
          </p>
          <button
            type="button"
            onClick={() => {
              reset();
              setFailed(null);
              setAttempt((a) => a + 1);
            }}
            className="mt-3 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:border-accent"
          >
            Check again
          </button>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="chat-heading" className="rounded-xl border border-line bg-surface p-5">
      <h2 id="chat-heading" className="font-display text-xl font-semibold">
        Chat about this episode
      </h2>
      <p className="mt-1 text-sm text-ink-muted">Follow-up questions keep the conversation going.</p>

      <div className="mt-4 space-y-4">
        {current === null ? (
          <TextSkeleton lines={2} label="Loading the conversation" />
        ) : historyUnavailable ? (
          <ProblemState
            problem={current.problem}
            onRetry={() => setAttempt((a) => a + 1)}
            retryLabel="Reload the conversation"
          />
        ) : null}

        {current?.kind === "ok" && items.length === 0 && !busy ? (
          <p className="text-sm text-ink-muted">No messages yet. Ask a follow-up below.</p>
        ) : null}

        <ol role="log" aria-live="polite" aria-label="Conversation" className="space-y-4 empty:hidden">
          {items.map((item) => (
            <Message key={item.message.id} item={item} audioUrl={audioUrl} />
          ))}
          {busy ? (
            <li className="space-y-2" aria-busy="true" data-testid="chat-pending">
              <span className="sr-only">PodderSum is answering: </span>
              {streamedClaims.length > 0 ? (
                <ClaimList claims={streamedClaims} audioUrl={audioUrl} />
              ) : (
                <TextSkeleton lines={1} label={state.status ?? "Thinking"} />
              )}
            </li>
          ) : null}
        </ol>

        {state.phase === "error" ? (
          <ProblemState
            compact
            problem={state.problem}
            onRetry={failed ? () => void send(failed) : undefined}
            retryLabel="Send again"
          />
        ) : null}

        <Composer
          label="Message"
          value={draft}
          onChange={setDraft}
          onSubmit={() => void send(draft)}
          busy={busy}
          submitLabel="Send"
          onCancel={cancel}
          rows={2}
          placeholder="Ask a follow-up"
        />
      </div>
    </section>
  );
}

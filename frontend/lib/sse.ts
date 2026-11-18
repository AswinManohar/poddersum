/**
 * Server-sent events over `fetch` + `ReadableStream` (contract v0, `backend/api/schemas.py`).
 *
 * `EventSource` cannot POST (ask, chat) and cannot be unit-tested without a browser, so
 * all streaming goes through `parseSSE`, which follows the WHATWG event-stream rules:
 * LF, CR and CRLF line endings (also split across chunks), `event:`/`id:`/`data:`/`retry:`
 * fields, multi-line `data`, comment lines (keep-alive pings) and unknown fields ignored.
 */
import { ApiError, apiBaseUrl, toApiError } from "./api";
import type { Answer, AnswerStreamEvent, JobEvent } from "./models";

export interface SSEMessage {
  /** `event:` field, `"message"` when absent. */
  event: string;
  /** `data:` lines joined with `\n`. */
  data: string;
  /** Last `id:` seen on the stream so far (persists across events, per spec). */
  id: string;
  /** `retry:` value of this event, when present. */
  retry?: number;
}

/** Parse an SSE byte stream into messages. Stops when the stream ends. */
export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEMessage> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let lastId = "";
  let eventType = "";
  let dataLines: string[] = [];
  let retry: number | undefined;
  let first = true;

  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  function processLine(line: string): SSEMessage | null {
    if (line === "") {
      const hasData = dataLines.length > 0;
      const msg: SSEMessage | null = hasData
        ? { event: eventType || "message", data: dataLines.join("\n"), id: lastId, ...(retry !== undefined ? { retry } : {}) }
        : null;
      eventType = "";
      dataLines = [];
      retry = undefined;
      return msg;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive ping
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        eventType = value;
        break;
      case "data":
        dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\u0000")) lastId = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) retry = Number(value);
        break;
      default:
        break; // unknown fields are ignored
    }
    return null;
  }

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
      } else {
        buffer += decoder.decode(value, { stream: true });
      }
      if (first && buffer.length > 0) {
        if (buffer.charCodeAt(0) === 0xfeff) buffer = buffer.slice(1);
        first = false;
      }
      // Split complete lines. A trailing CR may be the first half of a CRLF split across
      // chunks, so keep it in the buffer until more data (or the end) arrives.
      let start = 0;
      for (let i = 0; i < buffer.length; i++) {
        const ch = buffer[i];
        if (ch !== "\n" && ch !== "\r") continue;
        if (ch === "\r" && i === buffer.length - 1 && !done) break;
        const line = buffer.slice(start, i);
        if (ch === "\r" && buffer[i + 1] === "\n") i++;
        start = i + 1;
        const msg = processLine(line);
        if (msg) yield msg;
      }
      buffer = buffer.slice(start);
      if (done) return; // an unterminated final event is discarded, per spec
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

async function readErrorBody(res: Response): Promise<unknown> {
  try {
    const text = await res.text();
    return text === "" ? null : (parseJson(text) ?? text);
  } catch {
    return null;
  }
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

/** Resolve after `ms`, or reject when `signal` aborts. */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException ? err.name === "AbortError" : isRecord(err) && err.name === "AbortError";
}

// --------------------------------------------------------------------------- job progress

const TERMINAL_JOB_STATES = new Set<JobEvent["state"]>(["succeeded", "failed"]);

export function isTerminalJobState(state: JobEvent["state"]): boolean {
  return TERMINAL_JOB_STATES.has(state);
}

function isJobEvent(value: unknown): value is JobEvent {
  return (
    isRecord(value) &&
    typeof value.seq === "number" &&
    typeof value.job_id === "string" &&
    typeof value.state === "string" &&
    typeof value.step === "string" &&
    typeof value.progress === "number"
  );
}

export interface JobStreamOptions {
  /** Resume after this seq (0 or undefined replays everything). */
  lastEventId?: number;
  onEvent: (event: JobEvent) => void;
  signal?: AbortSignal;
  /** Consecutive failed connection attempts before giving up (default 6). */
  maxRetries?: number;
  /** Delay before reconnect attempt `n` (1-based), ms. Default 500 * 2^(n-1), max 8 s. */
  backoffMs?: (attempt: number) => number;
}

export const defaultBackoff = (attempt: number): number =>
  Math.min(8000, 500 * 2 ** (attempt - 1));

/**
 * Follow `GET /jobs/{id}/events` until the terminal (`succeeded`/`failed`) event and
 * resolve with it. Dropped connections are retried with backoff, resuming with
 * `Last-Event-ID` = last seen seq; duplicate seqs are ignored. 4xx responses are not
 * retried (an unknown job will not appear later) and reject with `ApiError`.
 */
export async function streamJobEvents(jobId: string, options: JobStreamOptions): Promise<JobEvent> {
  const { onEvent, signal, maxRetries = 6, backoffMs = defaultBackoff } = options;
  let lastSeq = options.lastEventId ?? 0;
  let failures = 0;
  let lastError: ApiError = new ApiError(0, "network_error", "Lost the connection to job progress.");
  const url = `${apiBaseUrl()}/jobs/${encodeURIComponent(jobId)}/events`;

  while (true) {
    if (signal?.aborted) throw abortError();
    if (failures > 0) {
      if (failures > maxRetries) throw lastError;
      await sleep(backoffMs(failures), signal);
    }
    let progressed = false;
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (lastSeq > 0) headers["Last-Event-ID"] = String(lastSeq);
      const res = await fetch(url, { headers, signal, cache: "no-store" });
      if (!res.ok) {
        const err = toApiError(res.status, await readErrorBody(res));
        if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) {
          throw err;
        }
        lastError = err;
        failures++;
        continue;
      }
      if (!res.body) {
        lastError = new ApiError(0, "network_error", "The progress stream had no body.");
        failures++;
        continue;
      }
      for await (const msg of parseSSE(res.body, signal)) {
        if (msg.event !== "job") continue;
        const data = parseJson(msg.data);
        if (!isJobEvent(data) || data.seq <= lastSeq) continue;
        lastSeq = data.seq;
        progressed = true;
        failures = 0;
        onEvent(data);
        if (isTerminalJobState(data.state)) return data;
      }
      // Stream ended without a terminal event: reconnect and resume.
      lastError = new ApiError(0, "network_error", "The progress stream closed early.");
      if (!progressed) failures++;
      else failures = 1;
    } catch (err) {
      if (isAbortError(err) || signal?.aborted) throw abortError();
      if (err instanceof ApiError) throw err;
      lastError = new ApiError(0, "network_error", "Lost the connection to job progress.");
      failures = progressed ? 1 : failures + 1;
    }
  }
}

// --------------------------------------------------------------------------- answers

const ANSWER_EVENTS = new Set(["status", "claim", "answer", "error", "done"]);

function isAnswerStreamEvent(value: unknown): value is AnswerStreamEvent {
  if (!isRecord(value) || typeof value.event !== "string" || !ANSWER_EVENTS.has(value.event)) {
    return false;
  }
  switch (value.event) {
    case "status":
      return typeof value.message === "string";
    case "claim":
      return typeof value.index === "number" && isRecord(value.claim);
    case "answer":
      return isRecord(value.answer) && Array.isArray(value.answer.claims);
    case "error":
      return typeof value.message === "string";
    default:
      return true;
  }
}

export interface AnswerStreamOptions {
  onEvent: (event: AnswerStreamEvent) => void;
  signal?: AbortSignal;
}

export interface AnswerStreamResult {
  answer: Answer | null;
  error: { message: string; code: string } | null;
}

/**
 * POST to an answer route (`/episodes/{id}/ask` or `/chat`) and dispatch its SSE events.
 * Not retried (answers are not replayable). Rejects with `ApiError` for a non-2xx
 * response (e.g. 409 `not_ready`, 501) and with code `stream_interrupted` when the stream
 * ends before `done`. An `error` event is a normal outcome, reported in the result.
 */
export async function streamAnswer(
  path: string,
  body: unknown,
  { onEvent, signal }: AnswerStreamOptions,
): Promise<AnswerStreamResult> {
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (err) {
    if (isAbortError(err) || signal?.aborted) throw abortError();
    throw new ApiError(0, "network_error", "Could not reach the PodderSum API. Is it running?");
  }
  if (!res.ok) throw toApiError(res.status, await readErrorBody(res));
  if (!res.body) throw new ApiError(0, "stream_interrupted", "The answer stream was empty.");

  const result: AnswerStreamResult = { answer: null, error: null };
  try {
    for await (const msg of parseSSE(res.body, signal)) {
      const data = parseJson(msg.data);
      if (!isAnswerStreamEvent(data)) continue;
      if (data.event === "answer") result.answer = data.answer;
      if (data.event === "error") result.error = { message: data.message, code: data.code ?? "error" };
      onEvent(data);
      if (data.event === "done") return result;
    }
  } catch (err) {
    if (isAbortError(err) || signal?.aborted) throw abortError();
    throw new ApiError(0, "stream_interrupted", "The connection dropped before the answer finished.");
  }
  if (signal?.aborted) throw abortError();
  // A stream that delivered its answer or error but lost the final `done` is still usable.
  if (result.answer || result.error) return result;
  throw new ApiError(0, "stream_interrupted", "The connection dropped before the answer finished.");
}

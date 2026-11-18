/** Test helpers: a routable `fetch` mock and SSE response builders. */
import { vi } from "vitest";
import type { Answer, AnswerStreamEvent, Claim, JobEvent, JobStatus } from "@/lib/models";

export const API = "http://localhost:8010";

export interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | null;
}

type Handler = (call: Call) => Response | Promise<Response>;

/** Install a fetch mock; returns the recorded calls. Handlers match `METHOD path`. */
export function mockFetch(routes: Record<string, Handler | Handler[]>) {
  const calls: Call[] = [];
  const counters = new Map<string, number>();
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((v, k) => {
      headers[k] = v;
    });
    const call: Call = { url, method, headers, body: typeof init?.body === "string" ? init.body : null };
    calls.push(call);
    const path = url.startsWith(API) ? url.slice(API.length) : url;
    const key = `${method} ${path}`;
    const entry = routes[key];
    if (!entry) return json(404, { detail: `no mock for ${key}`, code: "not_found" });
    if (Array.isArray(entry)) {
      const n = counters.get(key) ?? 0;
      counters.set(key, n + 1);
      return entry[Math.min(n, entry.length - 1)](call);
    }
    return entry(call);
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A stream of the given text chunks (encoded as UTF-8), then close. */
export function chunkStream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

/** A stream whose chunks are pushed by the test. */
export function controlledStream() {
  const enc = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c;
    },
  });
  return {
    stream,
    push: (text: string) => ctrl.enqueue(enc.encode(text)),
    close: () => ctrl.close(),
    error: (e: unknown) => ctrl.error(e),
  };
}

export function sse(stream: ReadableStream<Uint8Array>): Response {
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

export function jobFrame(e: JobEvent): string {
  return `event: job\nid: ${e.seq}\ndata: ${JSON.stringify(e)}\n\n`;
}

export function answerFrame(e: AnswerStreamEvent): string {
  return `event: ${e.event}\ndata: ${JSON.stringify(e)}\n\n`;
}

export const JOB_ID = "11111111-2222-3333-4444-555555555555";

export function jobEvent(seq: number, overrides: Partial<JobEvent> = {}): JobEvent {
  return {
    seq,
    job_id: JOB_ID,
    state: "running",
    step: "fetch_transcript",
    progress: 0.2,
    message: null,
    ts: "2026-11-13T10:00:00Z",
    ...overrides,
  };
}

export function jobStatus(episodeId: string, overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    job_id: JOB_ID,
    episode_id: episodeId,
    state: "queued",
    step: "queued",
    progress: 0,
    message: null,
    error: null,
    created_at: "2026-11-13T10:00:00Z",
    updated_at: "2026-11-13T10:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

export function claim(text: string, startMs: number, overrides: Partial<Claim> = {}): Claim {
  return {
    text,
    citations: [{ segment_id: 1 + Math.floor(startMs / 1000), start_ms: startMs, end_ms: null, speaker: "Kevin", quote: `quote for ${text}` }],
    section: null,
    ...overrides,
  };
}

export function answer(claims: Claim[], overrides: Partial<Answer> = {}): Answer {
  return {
    claims,
    model: "gemini-test",
    prompt_version: "v1",
    tokens_in: 1000,
    tokens_out: 100,
    cost_usd: 0.00038,
    cached: false,
    ...overrides,
  };
}

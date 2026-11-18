import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { AnswerStreamEvent, JobEvent } from "@/lib/models";
import { parseSSE, streamAnswer, streamJobEvents, type SSEMessage } from "@/lib/sse";
import {
  JOB_ID,
  answer,
  answerFrame,
  chunkStream,
  claim,
  controlledStream,
  json,
  jobEvent,
  jobFrame,
  mockFetch,
  sse,
} from "./http";

afterEach(() => {
  vi.unstubAllGlobals();
});

async function collect(chunks: string[]): Promise<SSEMessage[]> {
  const out: SSEMessage[] = [];
  for await (const m of parseSSE(chunkStream(chunks))) out.push(m);
  return out;
}

describe("parseSSE", () => {
  it("parses event, id and data fields", async () => {
    const msgs = await collect(["event: job\nid: 3\ndata: {\"a\":1}\n\n"]);
    expect(msgs).toEqual([{ event: "job", id: "3", data: '{"a":1}' }]);
  });

  it("handles chunk boundaries anywhere, including mid-field and mid-UTF-8", async () => {
    const text = "event: claim\ndata: café – ok\n\nevent: done\ndata: {}\n\n";
    const bytes = new TextEncoder().encode(text);
    // split into 1-byte chunks: every boundary, including inside multi-byte characters
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        for (const b of bytes) c.enqueue(new Uint8Array([b]));
        c.close();
      },
    });
    const out: SSEMessage[] = [];
    for await (const m of parseSSE(stream)) out.push(m);
    expect(out.map((m) => [m.event, m.data])).toEqual([
      ["claim", "café – ok"],
      ["done", "{}"],
    ]);
  });

  it("joins multi-line data with newlines", async () => {
    const msgs = await collect(["data: line one\ndata: line two\ndata:\n\n"]);
    expect(msgs[0].data).toBe("line one\nline two\n");
    expect(msgs[0].event).toBe("message");
  });

  it("ignores comments and keep-alive pings, and empty events", async () => {
    const msgs = await collect([": ping\n\n", ":\n", "event: job\n\n", "data: x\n\n", ": ping - 2026\n\n"]);
    expect(msgs).toEqual([{ event: "message", id: "", data: "x" }]);
  });

  it("accepts CRLF and lone CR line endings, also with CRLF split across chunks", async () => {
    const msgs = await collect(["event: a\r", "\ndata: 1\r\n\r", "\nevent: b\rdata: 2\r\r"]);
    expect(msgs.map((m) => [m.event, m.data])).toEqual([
      ["a", "1"],
      ["b", "2"],
    ]);
  });

  it("keeps the last id across events and strips one leading space only", async () => {
    const msgs = await collect(["id: 7\ndata:  two spaces\n\ndata:x\n\n"]);
    expect(msgs[0]).toMatchObject({ id: "7", data: " two spaces" });
    expect(msgs[1]).toMatchObject({ id: "7", data: "x" });
  });

  it("drops an unterminated final event", async () => {
    const msgs = await collect(["data: complete\n\ndata: partial"]);
    expect(msgs.map((m) => m.data)).toEqual(["complete"]);
  });

  it("reads retry and ignores unknown fields", async () => {
    const msgs = await collect(["retry: 3000\nfoo: bar\ndata: y\n\n"]);
    expect(msgs[0]).toEqual({ event: "message", id: "", data: "y", retry: 3000 });
  });
});

const noDelay = () => 0;

describe("streamJobEvents", () => {
  it("delivers events in order and resolves with the terminal event", async () => {
    mockFetch({
      [`GET /jobs/${JOB_ID}/events`]: () =>
        sse(
          chunkStream([
            ": ping\n\n",
            jobFrame(jobEvent(1, { state: "queued", step: "queued", progress: 0 })),
            jobFrame(jobEvent(2)),
            jobFrame(jobEvent(3, { state: "succeeded", step: "done", progress: 1 })),
          ]),
        ),
    });
    const seen: JobEvent[] = [];
    const last = await streamJobEvents(JOB_ID, { onEvent: (e) => seen.push(e), backoffMs: noDelay });
    expect(seen.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(last.state).toBe("succeeded");
  });

  it("reconnects after a drop, resumes with Last-Event-ID and ignores replayed seqs", async () => {
    const { calls } = mockFetch({
      [`GET /jobs/${JOB_ID}/events`]: [
        () => sse(chunkStream([jobFrame(jobEvent(1)), jobFrame(jobEvent(2))])), // closes early
        () => Promise.reject(new TypeError("network down")),
        () =>
          sse(
            chunkStream([
              jobFrame(jobEvent(2)), // duplicate, must be skipped
              jobFrame(jobEvent(3, { step: "segment", progress: 0.7 })),
              jobFrame(jobEvent(4, { state: "failed", step: "segment", message: "Bad VTT file." })),
              jobFrame(jobEvent(5)), // after the terminal event: never read
            ]),
          ),
      ],
    });
    const seen: number[] = [];
    const last = await streamJobEvents(JOB_ID, { onEvent: (e) => seen.push(e.seq), backoffMs: noDelay });
    expect(seen).toEqual([1, 2, 3, 4]);
    expect(last).toMatchObject({ state: "failed", message: "Bad VTT file." });
    expect(calls).toHaveLength(3);
    expect(calls[0].headers["last-event-id"]).toBeUndefined();
    expect(calls[1].headers["last-event-id"]).toBe("2");
    expect(calls[2].headers["last-event-id"]).toBe("2");
  });

  it("starts from a given lastEventId", async () => {
    const { calls } = mockFetch({
      [`GET /jobs/${JOB_ID}/events`]: () => sse(chunkStream([jobFrame(jobEvent(6, { state: "succeeded", step: "done", progress: 1 }))])),
    });
    await streamJobEvents(JOB_ID, { lastEventId: 5, onEvent: () => undefined, backoffMs: noDelay });
    expect(calls[0].headers["last-event-id"]).toBe("5");
  });

  it("retries 5xx but not 404, and gives up after maxRetries", async () => {
    mockFetch({ [`GET /jobs/${JOB_ID}/events`]: () => json(404, { detail: "Unknown job.", code: "not_found" }) });
    await expect(streamJobEvents(JOB_ID, { onEvent: () => undefined, backoffMs: noDelay })).rejects.toMatchObject({
      status: 404,
    });

    const { calls } = mockFetch({
      [`GET /jobs/${JOB_ID}/events`]: () => json(503, { detail: "Down.", code: "unavailable" }),
    });
    await expect(
      streamJobEvents(JOB_ID, { onEvent: () => undefined, backoffMs: noDelay, maxRetries: 2 }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(3);
  });

  it("stops when aborted", async () => {
    const ctl = controlledStream();
    mockFetch({ [`GET /jobs/${JOB_ID}/events`]: () => sse(ctl.stream) });
    const controller = new AbortController();
    const p = streamJobEvents(JOB_ID, { onEvent: () => controller.abort(), signal: controller.signal });
    ctl.push(jobFrame(jobEvent(1)));
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("streamAnswer", () => {
  const path = "/episodes/x/ask";

  it("dispatches status, claims and the answer, then resolves on done", async () => {
    const c1 = claim("First", 1000);
    const { calls } = mockFetch({
      [`POST ${path}`]: () =>
        sse(
          chunkStream([
            answerFrame({ event: "status", message: "Reading" }),
            answerFrame({ event: "claim", index: 0, claim: c1 }),
            answerFrame({ event: "answer", answer: answer([c1]) }),
            answerFrame({ event: "done" }),
          ]),
        ),
    });
    const events: AnswerStreamEvent["event"][] = [];
    const res = await streamAnswer(path, { question: "Q" }, { onEvent: (e) => events.push(e.event) });
    expect(events).toEqual(["status", "claim", "answer", "done"]);
    expect(res.answer?.claims).toEqual([c1]);
    expect(res.error).toBeNull();
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toBe('{"question":"Q"}');
    expect(calls[0].headers["content-type"]).toBe("application/json");
  });

  it("reports an error event followed by done as a result, not a throw", async () => {
    mockFetch({
      [`POST ${path}`]: () =>
        sse(
          chunkStream([
            answerFrame({ event: "error", message: "Gemini rejected GEMINI_API_KEY", code: "model_auth" }),
            answerFrame({ event: "done" }),
          ]),
        ),
    });
    const events: string[] = [];
    const res = await streamAnswer(path, { question: "Q" }, { onEvent: (e) => events.push(e.event) });
    expect(events).toEqual(["error", "done"]);
    expect(res).toEqual({ answer: null, error: { message: "Gemini rejected GEMINI_API_KEY", code: "model_auth" } });
  });

  it("throws ApiError for 409 and 501 responses", async () => {
    mockFetch({ [`POST ${path}`]: () => json(409, { detail: "Episode not ready.", code: "not_ready" }) });
    await expect(streamAnswer(path, {}, { onEvent: () => undefined })).rejects.toMatchObject({
      status: 409,
      code: "not_ready",
    });
    mockFetch({ [`POST ${path}`]: () => json(501, { detail: "Not implemented yet.", code: "not_implemented" }) });
    await expect(streamAnswer(path, {}, { onEvent: () => undefined })).rejects.toMatchObject({ status: 501 });
  });

  it("surfaces a dropped stream as stream_interrupted and does not retry", async () => {
    const { calls } = mockFetch({
      [`POST ${path}`]: () => sse(chunkStream([answerFrame({ event: "claim", index: 0, claim: claim("A", 0) })])),
    });
    await expect(streamAnswer(path, {}, { onEvent: () => undefined })).rejects.toMatchObject({
      code: "stream_interrupted",
    });
    expect(calls).toHaveLength(1);
  });

  it("maps a network failure to ApiError status 0", async () => {
    mockFetch({ [`POST ${path}`]: () => Promise.reject(new TypeError("Failed to fetch")) });
    await expect(streamAnswer(path, {}, { onEvent: () => undefined })).rejects.toMatchObject({
      status: 0,
      code: "network_error",
    });
  });
});

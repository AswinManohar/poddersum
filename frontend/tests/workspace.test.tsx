import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EpisodeWorkspace } from "@/components/EpisodeWorkspace";
import type { Artifact, ArtifactKind, ChatMessage, EpisodeStatus } from "@/lib/models";
import { episodeDetail } from "./fixtures";
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
  jobStatus,
  mockFetch,
  sse,
  type Call,
} from "./http";

const router = { refresh: vi.fn(), push: vi.fn(), replace: vi.fn(), back: vi.fn(), forward: vi.fn(), prefetch: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const EP = episodeDetail.id;
const AUDIO = episodeDetail.audio_url;
const base = `/episodes/${EP}`;

function artifact(kind: ArtifactKind, text: string, startMs = 62_000): Artifact {
  return {
    episode_id: EP,
    kind,
    prompt_version: "v1",
    answer: answer([claim(text, startMs, kind === "verdict" ? { section: "who_its_for" } : {})], { cached: true }),
    created_at: "2026-11-13T10:00:00Z",
  };
}

const notImplemented = () => json(501, { detail: "Not implemented yet (M0-S6).", code: "not_implemented" });

type Handler = (call: Call) => Response | Promise<Response>;

/** Routes every ready workspace hits on mount, plus presets. */
function readyRoutes(extra: Record<string, Handler | Handler[]> = {}): Record<string, Handler | Handler[]> {
  return {
    [`GET ${base}/artifacts/verdict`]: () => json(200, artifact("verdict", "Founders building AI products")),
    [`GET ${base}/artifacts/tldr`]: () => json(200, artifact("tldr", "They debate AI regulation")),
    [`GET ${base}/artifacts/takeaways`]: () => json(200, artifact("takeaways", "Regulation is coming")),
    [`GET ${base}/chat`]: () => json(200, [] satisfies ChatMessage[]),
    ...extra,
  };
}

function renderWorkspace(status: EpisodeStatus = "ready", activeJobId: string | null = null) {
  return render(<EpisodeWorkspace episodeId={EP} status={status} activeJobId={activeJobId} audioUrl={AUDIO} />);
}

const count = (calls: Call[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url.endsWith(path)).length;

beforeEach(() => {
  router.refresh.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EpisodeWorkspace: ready episode", () => {
  it("renders the worth-it verdict before the preset tabs", async () => {
    mockFetch(readyRoutes());
    renderWorkspace();

    const verdict = screen.getByRole("heading", { name: "Is this worth my time?" });
    const tabs = screen.getByRole("tablist", { name: "Summaries" });
    expect(verdict.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const panel = screen.getByTestId("verdict-panel");
    expect(await within(panel).findByText("Founders building AI products")).toBeInTheDocument();
    expect(within(panel).getByText("Who it is for")).toBeInTheDocument();
  });

  it("fetches a preset once per tab and serves it from memory when switching back", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(readyRoutes());
    renderWorkspace();

    // Nothing is fetched for the tabs until one is opened.
    await waitFor(() => expect(count(calls, "GET", "/artifacts/verdict")).toBe(1));
    expect(count(calls, "GET", "/artifacts/tldr")).toBe(0);

    await user.click(screen.getByRole("tab", { name: "TL;DR" }));
    expect(await screen.findByText("They debate AI regulation")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "TL;DR" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("tab", { name: "Takeaways" }));
    expect(await screen.findByText("Regulation is coming")).toBeInTheDocument();
    expect(screen.getByText("They debate AI regulation")).not.toBeVisible();

    await user.click(screen.getByRole("tab", { name: "TL;DR" }));
    expect(screen.getByText("They debate AI regulation")).toBeVisible();

    expect(count(calls, "GET", "/artifacts/tldr")).toBe(1);
    expect(count(calls, "GET", "/artifacts/takeaways")).toBe(1);
    expect(count(calls, "GET", "/artifacts/verdict")).toBe(1);
  });

  it("shows a clear message when a preset fails with 503", async () => {
    const user = userEvent.setup();
    mockFetch(
      readyRoutes({
        [`GET ${base}/artifacts/tldr`]: () => json(503, { detail: "Model provider timed out.", code: "unavailable" }),
      }),
    );
    renderWorkspace();
    await user.click(screen.getByRole("tab", { name: "TL;DR" }));
    const panel = screen.getByRole("tabpanel", { name: "TL;DR" });
    expect(await within(panel).findByText("The language model is unavailable")).toBeInTheDocument();
    expect(within(panel).getByText("Model provider timed out.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("EpisodeWorkspace: ask", () => {
  it("renders streamed claims as they arrive, then the final answer", async () => {
    const user = userEvent.setup();
    const stream = controlledStream();
    const { calls } = mockFetch(readyRoutes({ [`POST ${base}/ask`]: () => sse(stream.stream) }));
    renderWorkspace();

    await user.type(screen.getByLabelText("Your question"), "Summarize for a founder");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    const region = screen.getByTestId("ask-answer");
    expect(within(region).getByText("Summarize for a founder")).toBeInTheDocument();
    const askCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/ask"));
    expect(askCall?.body).toBe(JSON.stringify({ question: "Summarize for a founder" }));

    await act(async () => stream.push(answerFrame({ event: "status", message: "Reading the transcript" })));
    expect(await within(region).findAllByText("Reading the transcript")).not.toHaveLength(0);
    expect(within(region).queryAllByTestId("claim")).toHaveLength(0);

    const first = claim("Ship before you scale", 62_000);
    const second = claim("Hire for curiosity", 3_661_000);
    await act(async () => stream.push(answerFrame({ event: "claim", index: 0, claim: first })));
    expect(await within(region).findByText("Ship before you scale")).toBeInTheDocument();
    expect(within(region).getAllByTestId("claim")).toHaveLength(1);
    expect(within(region).queryByTestId("answer-footer")).toBeNull();

    await act(async () => stream.push(answerFrame({ event: "claim", index: 1, claim: second })));
    expect(await within(region).findByText("Hire for curiosity")).toBeInTheDocument();
    expect(within(region).getAllByTestId("claim")).toHaveLength(2);

    await act(async () => {
      stream.push(answerFrame({ event: "answer", answer: answer([first, second]) }));
      stream.push(answerFrame({ event: "done" }));
      stream.close();
    });
    const footer = await within(region).findByTestId("answer-footer");
    expect(footer).toHaveTextContent("gemini-test");
    expect(footer).toHaveTextContent("$0.0004");
    expect(within(region).getAllByTestId("claim")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument();
  });

  it("links every citation to the audio at its start time in a new tab", async () => {
    const user = userEvent.setup();
    const first = claim("Ship before you scale", 62_400);
    const second = claim("Hire for curiosity", 3_661_000);
    mockFetch(
      readyRoutes({
        [`POST ${base}/ask`]: () =>
          sse(
            chunkStream([
              answerFrame({ event: "claim", index: 0, claim: first }),
              answerFrame({ event: "claim", index: 1, claim: second }),
              answerFrame({ event: "answer", answer: answer([first, second]) }),
              answerFrame({ event: "done" }),
            ]),
          ),
      }),
    );
    renderWorkspace();
    await user.type(screen.getByLabelText("Your question"), "What is new?{Enter}");

    const region = screen.getByTestId("ask-answer");
    await within(region).findByTestId("answer-footer");
    const links = within(region).getAllByTestId("citation");
    expect(links).toHaveLength(2);

    expect(links[0]).toHaveAttribute("href", `${AUDIO}#t=62`);
    expect(links[0]).toHaveTextContent("1:02");
    expect(links[0]).toHaveAccessibleName("Listen from 1:02, Kevin (opens the audio in a new tab)");
    expect(links[1]).toHaveAttribute("href", `${AUDIO}#t=3661`);
    expect(links[1]).toHaveTextContent("1:01:01");
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("shows a stream error event as a message with a retry", async () => {
    const user = userEvent.setup();
    mockFetch(
      readyRoutes({
        [`POST ${base}/ask`]: () =>
          sse(
            chunkStream([
              answerFrame({ event: "status", message: "Thinking" }),
              answerFrame({ event: "error", message: "The model returned no usable answer.", code: "upstream_error" }),
              answerFrame({ event: "done" }),
            ]),
          ),
      }),
    );
    renderWorkspace();
    await user.type(screen.getByLabelText("Your question"), "Anything?{Enter}");

    const region = screen.getByTestId("ask-answer");
    const alert = await within(region).findByRole("alert");
    expect(alert).toHaveTextContent("The language model is unavailable");
    expect(alert).toHaveTextContent("The model returned no usable answer.");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("explains a 409 not_ready and a 502 model failure", async () => {
    const user = userEvent.setup();
    mockFetch(
      readyRoutes({
        [`POST ${base}/ask`]: [
          () => json(409, { detail: "Transcript is not ready.", code: "not_ready" }),
          () => json(502, { detail: "Upstream model error.", code: "upstream_error" }),
        ],
      }),
    );
    renderWorkspace();
    await user.type(screen.getByLabelText("Your question"), "First{Enter}");
    const region = screen.getByTestId("ask-answer");
    expect(await within(region).findByRole("alert")).toHaveTextContent("Transcript not ready");

    await user.click(within(region).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(within(region).getByRole("alert")).toHaveTextContent("The language model is unavailable"));
    expect(within(region).getByRole("alert")).toHaveTextContent("Upstream model error.");
  });
});

describe("EpisodeWorkspace: ingest progress", () => {
  it("starts ingest for a new episode, shows live progress, then switches to the ready layout", async () => {
    const events = controlledStream();
    const { calls } = mockFetch(
      readyRoutes({
        [`POST ${base}/ingest`]: () => json(202, jobStatus(EP)),
        [`GET /jobs/${JOB_ID}/events`]: () => sse(events.stream),
      }),
    );
    renderWorkspace("new");

    expect(screen.getByRole("heading", { name: "Preparing this episode" })).toBeInTheDocument();
    await waitFor(() => expect(count(calls, "POST", "/ingest")).toBe(1));
    await waitFor(() => expect(count(calls, "GET", "/events")).toBe(1));

    await act(async () =>
      events.push(jobFrame(jobEvent(1, { step: "transcribe", progress: 0.4, message: "Chunk 2 of 5" }))),
    );
    const bar = await screen.findByRole("progressbar", { name: "Processing progress" });
    await waitFor(() => expect(bar).toHaveAttribute("aria-valuenow", "40"));
    expect(screen.getByText("Transcribing the audio")).toBeInTheDocument();
    expect(screen.getByText("Chunk 2 of 5")).toBeInTheDocument();

    await act(async () => {
      events.push(jobFrame(jobEvent(2, { state: "succeeded", step: "done", progress: 1 })));
      events.close();
    });
    expect(await screen.findByRole("heading", { name: "Is this worth my time?" })).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(count(calls, "POST", "/ingest")).toBe(1);
  });

  it("reattaches to the running job after a refresh without starting a new one", async () => {
    const { calls } = mockFetch(
      readyRoutes({
        [`POST ${base}/ingest`]: () => json(202, jobStatus(EP)),
        // The server replays stored events from seq 1, then keeps the stream open.
        [`GET /jobs/${JOB_ID}/events`]: () => {
          const s = controlledStream();
          s.push(jobFrame(jobEvent(1, { step: "download_audio", progress: 0.1 })));
          s.push(jobFrame(jobEvent(2, { step: "transcribe", progress: 0.55 })));
          return sse(s.stream);
        },
      }),
    );
    // Server render after refresh: status processing with the active job id.
    renderWorkspace("processing", JOB_ID);

    const bar = await screen.findByRole("progressbar");
    await waitFor(() => expect(bar).toHaveAttribute("aria-valuenow", "55"));
    expect(screen.getByText("Transcribing the audio")).toBeInTheDocument();
    expect(count(calls, "POST", "/ingest")).toBe(0);
    const eventsCall = calls.find((c) => c.url.endsWith(`/jobs/${JOB_ID}/events`));
    expect(eventsCall?.headers["last-event-id"]).toBeUndefined();
  });

  it("falls back to the idempotent ingest POST when the job id is unknown", async () => {
    const { calls } = mockFetch(
      readyRoutes({
        [`POST ${base}/ingest`]: () => json(202, jobStatus(EP, { job_id: "99999999-2222-3333-4444-555555555555", state: "running", step: "segment", progress: 0.8 })),
        [`GET /jobs/${JOB_ID}/events`]: () => json(404, { detail: "Unknown job.", code: "not_found" }),
        "GET /jobs/99999999-2222-3333-4444-555555555555/events": () => sse(controlledStream().stream),
      }),
    );
    renderWorkspace("processing", JOB_ID);
    await waitFor(() => expect(count(calls, "POST", "/ingest")).toBe(1));
    expect(await screen.findByText("Splitting into timestamped segments")).toBeInTheDocument();
  });

  it("shows a failed job with a retry that starts a new attempt", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(
      readyRoutes({
        [`POST ${base}/ingest`]: [
          () => json(202, jobStatus(EP)),
          () => json(202, jobStatus(EP, { state: "succeeded", step: "done", progress: 1 })),
        ],
        [`GET /jobs/${JOB_ID}/events`]: () =>
          sse(chunkStream([jobFrame(jobEvent(1, { state: "failed", step: "transcribe", message: "Audio download returned 404." }))])),
      }),
    );
    renderWorkspace("new");
    expect(await screen.findByTestId("job-failed")).toHaveTextContent("Audio download returned 404.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Is this worth my time?" })).toBeInTheDocument();
    expect(count(calls, "POST", "/ingest")).toBe(2);
  });
});

describe("EpisodeWorkspace: chat", () => {
  const history: ChatMessage[] = [
    { id: "m1", role: "user", text: "Who is the guest?", claims: [], created_at: "2026-11-13T10:00:00Z" },
    {
      id: "m2",
      role: "assistant",
      text: "Kevin hosts with Casey.",
      claims: [claim("Kevin hosts with Casey", 5_000)],
      created_at: "2026-11-13T10:00:01Z",
    },
  ];

  it("reloads history on mount and streams a follow-up reply", async () => {
    const user = userEvent.setup();
    const stream = controlledStream();
    const { calls } = mockFetch(
      readyRoutes({
        [`GET ${base}/chat`]: () => json(200, history),
        [`POST ${base}/chat`]: () => sse(stream.stream),
      }),
    );
    renderWorkspace();

    const log = screen.getByRole("log", { name: "Conversation" });
    expect(await within(log).findByText("Who is the guest?")).toBeInTheDocument();
    expect(within(log).getByText("Kevin hosts with Casey")).toBeInTheDocument();
    expect(within(log).getByTestId("citation")).toHaveAttribute("href", `${AUDIO}#t=5`);

    await user.type(screen.getByLabelText("Message"), "What did they say about AI?{Enter}");
    expect(within(log).getByText("What did they say about AI?")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/chat"))?.body).toBe(
      JSON.stringify({ message: "What did they say about AI?" }),
    );

    const reply = claim("AI agents are overhyped", 125_000);
    await act(async () => stream.push(answerFrame({ event: "claim", index: 0, claim: reply })));
    const pending = await within(log).findByTestId("chat-pending");
    expect(within(pending).getByText("AI agents are overhyped")).toBeInTheDocument();

    await act(async () => {
      stream.push(answerFrame({ event: "answer", answer: answer([reply]) }));
      stream.push(answerFrame({ event: "done" }));
      stream.close();
    });
    await waitFor(() => expect(within(log).queryByTestId("chat-pending")).toBeNull());
    const assistants = within(log).getAllByTestId("chat-assistant");
    expect(assistants).toHaveLength(2);
    expect(within(assistants[1]).getByText("AI agents are overhyped")).toBeInTheDocument();
    expect(within(assistants[1]).getByTestId("citation")).toHaveAttribute("href", `${AUDIO}#t=125`);
  });

  it("shows a friendly 'not available yet' state when chat returns 501", async () => {
    mockFetch(readyRoutes({ [`GET ${base}/chat`]: notImplemented, [`POST ${base}/chat`]: notImplemented }));
    renderWorkspace();
    const notice = await screen.findByTestId("chat-unavailable");
    expect(notice).toHaveTextContent("Chat is not available yet");
    expect(notice).toHaveAttribute("role", "status");
    expect(screen.queryByLabelText("Message")).toBeNull();
    // The rest of the page still works.
    expect(screen.getByLabelText("Your question")).toBeInTheDocument();
  });

  it("switches to the same state when only sending returns 501", async () => {
    const user = userEvent.setup();
    mockFetch(readyRoutes({ [`POST ${base}/chat`]: notImplemented }));
    renderWorkspace();
    await screen.findByText("No messages yet. Ask a follow-up below.");
    await user.type(screen.getByLabelText("Message"), "Hello{Enter}");
    expect(await screen.findByTestId("chat-unavailable")).toBeInTheDocument();
  });
});

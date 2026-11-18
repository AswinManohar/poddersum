/**
 * Tiny stand-in for the PodderSum API used by the Playwright flow (contract v0,
 * `backend/api/schemas.py`). It serves one seeded, ready episode so that both the
 * server components (which fetch the episode on the Next.js server) and the browser
 * talk to the same origin. Usage: `node e2e/mock-api.mjs <port>`.
 *
 * Routes: GET /healthz, GET /episodes/{id}, GET /episodes/{id}/artifacts/{kind},
 * POST /episodes/{id}/ask (SSE, claims spaced out so streaming is observable),
 * GET|POST /episodes/{id}/chat (501, as the backend does until M0-S6).
 */
import http from "node:http";
import { SEED } from "./seed.mjs";

const port = Number(process.argv[2] ?? process.env.E2E_API_PORT ?? 0);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Accept, Last-Event-ID",
};

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json", ...CORS });
  res.end(JSON.stringify(body));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function answer(claims) {
  return {
    claims,
    model: "mock-model",
    prompt_version: "v1",
    tokens_in: 1200,
    tokens_out: 80,
    cost_usd: 0.00038,
    cached: false,
  };
}

function artifact(kind) {
  return {
    episode_id: SEED.episode.id,
    kind,
    prompt_version: "v1",
    answer: { ...answer(SEED.artifacts[kind]), cached: true },
    created_at: "2026-11-13T10:00:00Z",
  };
}

async function streamAsk(req, res) {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  let question = "";
  try {
    question = String(JSON.parse(raw).question ?? "");
  } catch {
    return send(res, 422, { detail: [{ msg: "Invalid JSON" }] });
  }
  if (question.trim() === "") return send(res, 422, { detail: [{ msg: "question is required" }] });

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    ...CORS,
  });
  const frame = (data) => res.write(`event: ${data.event}\ndata: ${JSON.stringify(data)}\n\n`);
  frame({ event: "status", message: "Reading the transcript" });
  await sleep(150);
  for (const [index, claim] of SEED.askClaims.entries()) {
    frame({ event: "claim", index, claim });
    await sleep(400);
  }
  frame({ event: "answer", answer: answer(SEED.askClaims) });
  frame({ event: "done" });
  res.end();
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const path = url.pathname;
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS);
    return res.end();
  }
  if (path === "/healthz") return send(res, 200, { status: "ok" });

  const ep = `/episodes/${SEED.episode.id}`;
  if (req.method === "GET" && path === ep) return send(res, 200, SEED.episode);
  const art = path.match(/^\/episodes\/([0-9a-f]{64})\/artifacts\/(tldr|takeaways|verdict)$/);
  if (req.method === "GET" && art && art[1] === SEED.episode.id) return send(res, 200, artifact(art[2]));
  if (req.method === "POST" && path === `${ep}/ask`) {
    streamAsk(req, res).catch(() => res.end());
    return;
  }
  if (path === `${ep}/chat`) return send(res, 501, { detail: "Not implemented yet (M0-S6).", code: "not_implemented" });
  return send(res, 404, { detail: "Not found.", code: "not_found" });
});

server.listen(port, "127.0.0.1", () => {
  const address = server.address();
  const actual = typeof address === "object" && address ? address.port : port;
  console.log(`mock API listening on http://127.0.0.1:${actual}`);
});

for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));

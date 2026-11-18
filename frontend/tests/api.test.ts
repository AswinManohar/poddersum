import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getEpisode, getPodcastEpisodes, searchPodcasts, toApiError } from "@/lib/api";
import { podcast } from "./fixtures";

function mockFetch(status: number, body: unknown, asText = false) {
  const fn = vi.fn<typeof fetch>(async () =>
    new Response(asText ? String(body) : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("toApiError", () => {
  it("maps an ErrorResponse body", () => {
    const err = toApiError(404, { detail: "Podcast not found.", code: "not_found" });
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "not_found", detail: "Podcast not found." });
    expect(err.isNotFound).toBe(true);
    expect(err.message).toBe("Podcast not found.");
  });

  it("maps FastAPI's 422 validation body", () => {
    const err = toApiError(422, { detail: [{ loc: ["query", "q"], msg: "Field required", type: "missing" }] });
    expect(err).toMatchObject({ status: 422, code: "validation_error", detail: "Field required" });
  });

  it("falls back for non-JSON or unknown bodies", () => {
    expect(toApiError(502, "Bad gateway")).toMatchObject({ code: "upstream_error", detail: "Request failed (HTTP 502)." });
    expect(toApiError(500, null)).toMatchObject({ code: "server_error" });
    expect(toApiError(418, { detail: "teapot" })).toMatchObject({ code: "http_error", detail: "teapot" });
  });
});

describe("client", () => {
  it("searchPodcasts calls /search with the query and parses the body", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test/");
    const fn = mockFetch(200, { query: "hard fork", results: [podcast] });
    const res = await searchPodcasts("hard fork", 5);
    expect(res.results[0].title).toBe("Hard Fork");
    expect(fn.mock.calls[0][0]).toBe("http://api.test/search?q=hard+fork&limit=5");
  });

  it("defaults the base URL to localhost:8010", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    const fn = mockFetch(200, { podcast, episodes: [], total: 0 });
    await getPodcastEpisodes(podcast.id, 10);
    expect(fn.mock.calls[0][0]).toBe(`http://localhost:8010/podcasts/${podcast.id}/episodes?limit=10`);
  });

  it("throws ApiError with status, code and detail on non-2xx", async () => {
    mockFetch(404, { detail: "Episode not found.", code: "not_found" });
    await expect(getEpisode("x")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      code: "not_found",
      detail: "Episode not found.",
    });
  });

  it("throws a network_error ApiError when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => { throw new TypeError("fetch failed"); }));
    await expect(getEpisode("x")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("handles a non-JSON error body", async () => {
    mockFetch(503, "<html>down</html>", true);
    await expect(searchPodcasts("x")).rejects.toMatchObject({ status: 503, code: "unavailable" });
  });
});

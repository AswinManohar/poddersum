/**
 * Typed client for the PodderSum API (contract v0, `backend/api/schemas.py`).
 * Works in server components and in the browser. Streaming (SSE) routes live in
 * `lib/sse.ts`.
 */
import type {
  Artifact,
  ArtifactKind,
  ChatMessage,
  EpisodeDetail,
  ErrorResponse,
  JobStatus,
  PodcastEpisodes,
  SearchResponse,
} from "./models";

const DEFAULT_API_BASE_URL = "http://localhost:8010";

export function apiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL;
  const base = raw && raw.trim() !== "" ? raw.trim() : DEFAULT_API_BASE_URL;
  return base.replace(/\/+$/, "");
}

/** A non-2xx response (or a network failure, `status` 0) from the API. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;

  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  return isRecord(value) && typeof value.detail === "string" && typeof value.code === "string";
}

const FALLBACK_CODES: Record<number, string> = {
  400: "bad_request",
  404: "not_found",
  409: "conflict",
  422: "validation_error",
  429: "rate_limited",
  501: "not_implemented",
  502: "upstream_error",
  503: "unavailable",
};

/** Map an error response body to an ApiError. Handles `ErrorResponse`, FastAPI's 422
 * validation body (`detail` is a list) and non-JSON bodies. */
export function toApiError(status: number, body: unknown): ApiError {
  if (isErrorResponse(body)) {
    return new ApiError(status, body.code, body.detail);
  }
  const code = FALLBACK_CODES[status] ?? (status >= 500 ? "server_error" : "http_error");
  if (isRecord(body) && Array.isArray(body.detail)) {
    const first: unknown = body.detail[0];
    const msg = isRecord(first) && typeof first.msg === "string" ? first.msg : "Invalid request.";
    return new ApiError(status, code, msg);
  }
  if (isRecord(body) && typeof body.detail === "string") {
    return new ApiError(status, code, body.detail);
  }
  return new ApiError(status, code, `Request failed (HTTP ${status}).`);
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (text === "") return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the PodderSum API. Is it running?");
  }
  const body = await readBody(res);
  if (!res.ok) throw toApiError(res.status, body);
  return body as T;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export function searchPodcasts(
  q: string,
  limit?: number,
  options: RequestOptions = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q });
  if (limit !== undefined) params.set("limit", String(limit));
  return getJson<SearchResponse>(`/search?${params.toString()}`, { signal: options.signal });
}

export function getPodcastEpisodes(podcastId: string, limit?: number): Promise<PodcastEpisodes> {
  const query = limit !== undefined ? `?limit=${limit}` : "";
  return getJson<PodcastEpisodes>(`/podcasts/${encodeURIComponent(podcastId)}/episodes${query}`);
}

export function getEpisode(episodeId: string): Promise<EpisodeDetail> {
  return getJson<EpisodeDetail>(`/episodes/${encodeURIComponent(episodeId)}`);
}

const ep = (episodeId: string) => `/episodes/${encodeURIComponent(episodeId)}`;

/** Start (or join) the ingest job for an episode. Idempotent on the server. */
export function startIngest(episodeId: string, options: RequestOptions = {}): Promise<JobStatus> {
  return getJson<JobStatus>(`${ep(episodeId)}/ingest`, { method: "POST", signal: options.signal });
}

export function getJob(jobId: string, options: RequestOptions = {}): Promise<JobStatus> {
  return getJson<JobStatus>(`/jobs/${encodeURIComponent(jobId)}`, { signal: options.signal });
}

/** A cached preset answer; the first call may take several seconds (generation). */
export function getArtifact(
  episodeId: string,
  kind: ArtifactKind,
  options: RequestOptions = {},
): Promise<Artifact> {
  return getJson<Artifact>(`${ep(episodeId)}/artifacts/${kind}`, {
    signal: options.signal,
    cache: "no-store",
  });
}

/** Episode chat history, oldest first. */
export function getChatHistory(episodeId: string, options: RequestOptions = {}): Promise<ChatMessage[]> {
  return getJson<ChatMessage[]>(`${ep(episodeId)}/chat`, { signal: options.signal, cache: "no-store" });
}

export const askPath = (episodeId: string) => `${ep(episodeId)}/ask`;
export const chatPath = (episodeId: string) => `${ep(episodeId)}/chat`;

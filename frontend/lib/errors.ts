/**
 * Turn API failures into words a listener can act on. Used by every panel's error state.
 */
import { ApiError } from "./api";

export type ProblemKind =
  | "model_auth"
  | "not_available"
  | "not_ready"
  | "network"
  | "interrupted"
  | "model_unavailable"
  | "rate_limited"
  | "generic";

export interface Problem {
  kind: ProblemKind;
  title: string;
  message: string;
  /** The server's own text (`detail` / `message`), shown as a secondary line. */
  detail: string | null;
  /** Retrying may help (false for configuration problems that need an operator). */
  retryable: boolean;
}

/** An `error` event of an answer stream. */
export interface StreamErrorLike {
  message: string;
  code: string;
}

const AUTH_CODES = new Set([
  "model_auth",
  "model_auth_error",
  "llm_auth",
  "llm_auth_error",
  "auth_error",
  "invalid_api_key",
  "model_config",
  "llm_config",
]);

const AUTH_TEXT = /GEMINI_API_KEY|API[_ ]?KEY[_ ]?INVALID|api key (?:was )?(?:rejected|invalid|not valid)|rejected (?:the )?(?:api )?key/i;

function problem(kind: ProblemKind, detail: string | null): Problem {
  switch (kind) {
    case "model_auth":
      return {
        kind,
        title: "The language model is not configured",
        message:
          "The language model key was rejected, so answers cannot be generated right now. Set a valid GEMINI_API_KEY in .env and restart the API.",
        detail,
        retryable: true,
      };
    case "not_available":
      return {
        kind,
        title: "Not available yet",
        message: "This part of PodderSum is not available yet.",
        detail,
        retryable: true,
      };
    case "not_ready":
      return {
        kind,
        title: "Transcript not ready",
        message: "This episode has not finished processing yet.",
        detail,
        retryable: true,
      };
    case "network":
      return {
        kind,
        title: "Could not reach PodderSum",
        message: "The PodderSum API did not answer. Check that it is running, then try again.",
        detail,
        retryable: true,
      };
    case "interrupted":
      return {
        kind,
        title: "The answer was interrupted",
        message: "The connection dropped before the answer finished. Try again.",
        detail,
        retryable: true,
      };
    case "model_unavailable":
      return {
        kind,
        title: "The language model is unavailable",
        message: "The language model did not respond. Try again in a moment.",
        detail,
        retryable: true,
      };
    case "rate_limited":
      return {
        kind,
        title: "Too many requests",
        message: "Please wait a moment, then try again.",
        detail,
        retryable: true,
      };
    default:
      return {
        kind: "generic",
        title: "Something went wrong",
        message: "This could not be loaded.",
        detail,
        retryable: true,
      };
  }
}

function isAuthProblem(code: string, text: string): boolean {
  return AUTH_CODES.has(code) || AUTH_TEXT.test(text);
}

/** Describe an `ApiError`, an answer-stream `error` event, or anything thrown. */
export function describeProblem(err: unknown): Problem {
  if (err instanceof ApiError) {
    const detail = err.detail || null;
    if (isAuthProblem(err.code, err.detail)) return problem("model_auth", detail);
    if (err.status === 501 || err.code === "not_implemented") return problem("not_available", detail);
    if (err.code === "not_ready") return problem("not_ready", detail);
    if (err.code === "stream_interrupted") return problem("interrupted", detail);
    if (err.status === 0) return problem("network", detail);
    if (err.status === 409) return problem("not_ready", detail);
    if (err.status === 429) return problem("rate_limited", detail);
    if (err.status === 502 || err.status === 503 || err.status === 504) {
      return problem("model_unavailable", detail);
    }
    return problem("generic", detail);
  }
  if (isStreamError(err)) {
    if (isAuthProblem(err.code, err.message)) return problem("model_auth", err.message);
    if (err.code === "not_implemented") return problem("not_available", err.message);
    if (err.code === "not_ready") return problem("not_ready", err.message);
    if (err.code === "rate_limited") return problem("rate_limited", err.message);
    if (err.code === "upstream_error" || err.code === "unavailable") {
      return problem("model_unavailable", err.message);
    }
    return { ...problem("generic", err.message), message: "The answer could not be generated." };
  }
  return problem("generic", err instanceof Error ? err.message : null);
}

function isStreamError(value: unknown): value is StreamErrorLike {
  return (
    typeof value === "object" &&
    value !== null &&
    !(value instanceof Error) &&
    typeof (value as Record<string, unknown>).message === "string" &&
    typeof (value as Record<string, unknown>).code === "string"
  );
}

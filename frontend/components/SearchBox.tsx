"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { ApiError, searchPodcasts } from "@/lib/api";
import type { PodcastHit } from "@/lib/models";
import { Notice } from "./Notice";
import { PodcastCard } from "./PodcastCard";
import { ListSkeleton } from "./Skeleton";

export const SEARCH_DEBOUNCE_MS = 350;

type Result =
  | { key: string; kind: "ok"; results: PodcastHit[] }
  | { key: string; kind: "error"; message: string };

/**
 * Search box plus results. The committed query is mirrored into `?q=` (replace, not push)
 * so results survive refresh and coming back from a show page.
 */
export function SearchBox() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initial = searchParams.get("q") ?? "";
  const inputId = useId();
  const hintId = useId();

  const [input, setInput] = useState(initial);
  const [query, setQuery] = useState(initial.trim());
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const key = `${attempt}:${query}`;

  // Debounce typing into the committed query.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(input.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [input]);

  // Mirror the committed query into the URL.
  useEffect(() => {
    const current = new URLSearchParams(window.location.search).get("q") ?? "";
    if (current === query) return;
    router.replace(query ? `/?q=${encodeURIComponent(query)}` : "/", { scroll: false });
  }, [query, router]);

  // Fetch results for the committed query.
  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    searchPodcasts(query, undefined, { signal: controller.signal })
      .then((res) => setResult({ key, kind: "ok", results: res.results }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          err instanceof ApiError ? err.detail : "Something went wrong while searching.";
        setResult({ key, kind: "error", message });
      });
    return () => controller.abort();
  }, [key, query]);

  const current = result?.key === key ? result : null;

  return (
    <div className="space-y-6">
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(input.trim());
        }}
      >
        <label htmlFor={inputId} className="mb-2 block font-medium">
          Find a podcast
        </label>
        <div className="flex gap-2">
          <input
            id={inputId}
            type="search"
            name="q"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Show name, e.g. Hard Fork"
            autoComplete="off"
            spellCheck={false}
            aria-describedby={hintId}
            className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-4 py-3 text-base text-ink placeholder:text-ink-muted"
          />
          <button
            type="submit"
            className="rounded-lg bg-accent px-4 py-3 font-medium text-accent-ink hover:opacity-90"
          >
            Search
          </button>
        </div>
        <p id={hintId} className="mt-2 text-sm text-ink-muted">
          Results update as you type. Any public podcast with an RSS feed works.
        </p>
      </form>

      <section aria-label="Search results" aria-live="polite" aria-busy={query !== "" && !current}>
        {query === "" ? null : !current ? (
          <ListSkeleton rows={4} label="Searching" />
        ) : current.kind === "error" ? (
          <Notice title="Search failed" tone="error">
            <p>{current.message}</p>
            <button
              type="button"
              onClick={() => setAttempt((n) => n + 1)}
              className="mt-3 font-medium text-accent underline underline-offset-4"
            >
              Try again
            </button>
          </Notice>
        ) : current.results.length === 0 ? (
          <Notice title="No shows found">
            <p>
              Nothing matches &ldquo;{query}&rdquo;. Check the spelling or try the host&rsquo;s
              name.
            </p>
          </Notice>
        ) : (
          <ul className="space-y-3">
            {current.results.map((podcast) => (
              <li key={podcast.id}>
                <PodcastCard podcast={podcast} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** Static stand-in while the client reads the URL. */
export function SearchBoxFallback() {
  return (
    <div className="space-y-2">
      <p className="mb-2 font-medium">Find a podcast</p>
      <div className="h-12 rounded-lg border border-line bg-surface" />
    </div>
  );
}

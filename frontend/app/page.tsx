import { Suspense } from "react";
import { SearchBox, SearchBoxFallback } from "@/components/SearchBox";

export default function HomePage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
          Is this episode worth your time?
        </h1>
        <p className="mt-3 max-w-prose text-ink-muted">
          Search a show, pick an episode, and ask for a summary from any angle. Every claim links
          to the moment it was said.
        </p>
      </div>
      <Suspense fallback={<SearchBoxFallback />}>
        <SearchBox />
      </Suspense>
    </div>
  );
}

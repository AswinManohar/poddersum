import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="border-b border-line">
      <div className="mx-auto flex w-full max-w-3xl items-center justify-between px-4 py-4 sm:px-6">
        <Link href="/" className="group inline-flex items-baseline" aria-label="PodderSum home">
          <span className="font-display text-2xl font-semibold tracking-tight text-ink">
            Podder
          </span>
          <span className="font-display text-2xl font-semibold italic tracking-tight text-accent">
            Sum
          </span>
          <span aria-hidden="true" className="ml-1 inline-block h-2 w-2 rounded-full bg-accent" />
        </Link>
        <p className="hidden text-sm text-ink-muted sm:block">Cited summaries of any podcast</p>
      </div>
    </header>
  );
}

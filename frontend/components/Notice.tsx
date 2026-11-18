import Link from "next/link";

interface NoticeProps {
  title: string;
  children?: React.ReactNode;
  tone?: "neutral" | "error";
  action?: { href: string; label: string };
}

/** Empty, error and "not found" states. */
export function Notice({ title, children, tone = "neutral", action }: NoticeProps) {
  const border = tone === "error" ? "border-danger/40 bg-danger-soft" : "border-line bg-surface";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`rounded-xl border p-6 ${border}`}>
      <h2 className="font-display text-lg font-semibold">{title}</h2>
      {children ? <div className="mt-2 text-ink-muted">{children}</div> : null}
      {action ? (
        <Link
          href={action.href}
          className="mt-4 inline-block font-medium text-accent underline underline-offset-4"
        >
          {action.label}
        </Link>
      ) : null}
    </div>
  );
}

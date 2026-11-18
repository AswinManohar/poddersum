interface ArtworkProps {
  src: string | null | undefined;
  alt: string;
  size: number;
  className?: string;
}

/** Remote podcast artwork. Hosts are arbitrary, so this is a plain lazy <img> rather than
 * next/image; a neutral tile stands in when there is no artwork. */
export function Artwork({ src, alt, size, className = "" }: ArtworkProps) {
  const box = `shrink-0 rounded-lg border border-line bg-surface-muted ${className}`;
  if (!src) {
    return (
      <div
        aria-hidden="true"
        className={`${box} flex items-center justify-center font-display text-ink-muted`}
        style={{ width: size, height: size, fontSize: Math.round(size / 3) }}
      >
        P
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- arbitrary remote hosts
    <img
      src={src}
      alt={alt}
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      className={`${box} object-cover`}
      style={{ width: size, height: size }}
    />
  );
}

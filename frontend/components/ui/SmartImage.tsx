"use client";

import { useEffect, useRef, useState } from "react";

const POSITIONED = /\b(absolute|fixed)\b/;

/**
 * Third-party image with a placeholder until it loads and a permanent fallback when it fails.
 * Plain <img>: sources are arbitrary search-result hosts, so next/image's allow-list does not fit.
 */
export function SmartImage({
  src,
  alt,
  label,
  className = "",
  onFail,
  eager = false,
}: {
  src: string | null | undefined;
  alt: string;
  label?: string;
  className?: string;
  onFail?: () => void;
  eager?: boolean;
}) {
  const [state, setState] = useState<"loading" | "ready" | "failed">(src ? "loading" : "failed");
  const ref = useRef<HTMLImageElement>(null);

  // A cached image can finish before hydration attaches onLoad.
  useEffect(() => {
    const img = ref.current;
    if (!img?.complete) return;
    if (img.naturalWidth > 0) {
      setState("ready");
    } else {
      setState("failed");
      onFail?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Callers may position the frame themselves (e.g. absolute hero backgrounds).
  const position = POSITIONED.test(className) ? "" : "relative";

  return (
    <div className={`${position} overflow-hidden bg-gradient-to-br from-surface-2 via-surface to-bg ${className}`}>
      {state !== "ready" ? (
        <div className="absolute inset-0 flex items-end p-4">
          <span className="font-display text-sm text-muted/70">{label ?? alt}</span>
        </div>
      ) : null}
      {src && state !== "failed" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          ref={ref}
          src={src}
          alt={alt}
          loading={eager ? "eager" : "lazy"}
          decoding="async"
          referrerPolicy="no-referrer"
          onLoad={() => setState("ready")}
          onError={() => {
            setState("failed");
            onFail?.();
          }}
          className={`h-full w-full object-cover transition-opacity duration-500 ${
            state === "ready" ? "opacity-100" : "opacity-0"
          }`}
        />
      ) : null}
    </div>
  );
}

import { sourceLabel } from "@/lib/format";
import type { SpecValue } from "@/lib/types";

const DOT: Record<string, string> = {
  official_brochure: "bg-good",
  official_website: "bg-good",
  manual: "bg-accent",
  legacy_v1: "bg-warn",
  third_party: "bg-warn",
};

/** A small marker; hover or focus reveals original wording, source, page and confidence. */
export function SourceBadge({ spec }: { spec: SpecValue }) {
  const source = spec.source;
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        className="flex h-5 w-5 items-center justify-center rounded-full"
        aria-label={`Source: ${sourceLabel(source.type)}`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${DOT[source.type] ?? "bg-muted"}`} />
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute right-0 top-6 z-20 hidden w-72 rounded-xl border border-line bg-surface-2 p-3 text-left text-xs text-muted shadow-2xl group-focus-within:block group-hover:block"
      >
        <span className="block font-medium text-text">{sourceLabel(source.type)}</span>
        {source.brochure_title || source.page ? (
          <span className="mt-1 block">
            {source.brochure_title ?? "Brochure"}
            {source.page ? `, page ${source.page}` : ""}
          </span>
        ) : null}
        {spec.original_value ? <span className="mt-1 block">As printed: “{spec.original_value}”</span> : null}
        {spec.confidence !== null ? (
          <span className="mt-1 block">Extraction confidence {Math.round(spec.confidence * 100)}%</span>
        ) : null}
        {spec.flags.length ? <span className="mt-1 block text-warn">Note: {spec.flags.join(", ")}</span> : null}
      </span>
    </span>
  );
}

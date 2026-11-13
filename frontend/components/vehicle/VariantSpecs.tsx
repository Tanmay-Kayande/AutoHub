"use client";

import { useEffect, useMemo, useState } from "react";

import { ErrorState, Skeleton } from "@/components/ui/States";
import { SourceBadge } from "@/components/vehicle/SourceBadge";
import { ApiError, request } from "@/lib/api";
import { formatPrice, label } from "@/lib/format";
import type { SpecCategory, SpecValue, Variant, VariantSpecs as Specs } from "@/lib/types";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; specs: Specs };

function displayValue(spec: SpecValue): string {
  if (spec.status === "not_applicable") return "N/A";
  if (spec.status === "not_available") return "No";
  if (spec.status === "optional") return "Optional";
  return spec.display || "Not specified";
}

export function VariantSpecs({
  variants,
  categories,
  onVariantChange,
}: {
  variants: Variant[];
  categories: SpecCategory[];
  onVariantChange?: (variant: Variant) => void;
}) {
  const [variantId, setVariantId] = useState<number | null>(variants[0]?.id ?? null);
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [tab, setTab] = useState(categories[0]?.key ?? "");
  const [showMissing, setShowMissing] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const variant = variants.find((v) => v.id === variantId) ?? null;

  useEffect(() => {
    if (variantId === null) return;
    let cancelled = false;
    setLoad({ state: "loading" });
    request<Specs>(`/variants/${variantId}/specs`)
      .then((specs) => !cancelled && setLoad({ state: "ready", specs }))
      .catch(
        (error) =>
          !cancelled &&
          setLoad({ state: "error", message: error instanceof ApiError ? error.message : "Specifications could not be loaded." }),
      );
    return () => {
      cancelled = true;
    };
  }, [variantId, attempt]);

  const byKey = useMemo(() => {
    const map = new Map<string, SpecValue>();
    if (load.state === "ready") for (const group of load.specs.groups) for (const spec of group.specs) map.set(spec.key, spec);
    return map;
  }, [load]);

  if (!variants.length) return <p className="text-muted">No variants are recorded for this model yet.</p>;

  const category = categories.find((c) => c.key === tab) ?? categories[0];
  const present = category ? category.definitions.filter((d) => byKey.has(d.key)) : [];
  const missing = category ? category.definitions.filter((d) => !byKey.has(d.key)) : [];
  const countFor = (c: SpecCategory) => c.definitions.filter((d) => byKey.has(d.key)).length;

  return (
    <div>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs uppercase tracking-[0.15em] text-muted">Variant</span>
          <select
            value={variantId ?? ""}
            onChange={(event) => {
              const id = Number(event.target.value);
              setVariantId(id);
              const next = variants.find((v) => v.id === id);
              if (next) onVariantChange?.(next);
            }}
            className="w-full max-w-md rounded-xl border border-line bg-surface px-3 py-2.5 text-text md:w-96"
          >
            {variants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        {variant ? (
          <p className="text-sm text-muted">
            {[label(variant.fuel_type), label(variant.transmission_type), label(variant.drivetrain)].filter(Boolean).join(" · ")}
            {variant.price_ex_showroom ? (
              <span className="ml-3 text-text">{formatPrice(variant.price_ex_showroom)} ex-showroom</span>
            ) : null}
          </p>
        ) : null}
      </div>

      <div role="tablist" aria-label="Specification categories" className="no-scrollbar mt-8 flex gap-1 overflow-x-auto border-b border-line">
        {categories.map((c) => (
          <button
            key={c.key}
            role="tab"
            id={`tab-${c.key}`}
            aria-selected={c.key === category?.key}
            aria-controls={`panel-${c.key}`}
            onClick={() => setTab(c.key)}
            className={`shrink-0 border-b-2 px-3 py-3 text-sm ${
              c.key === category?.key ? "border-accent text-text" : "border-transparent text-muted hover:text-text"
            }`}
          >
            {c.name}
            {load.state === "ready" ? <span className="ml-1.5 text-xs text-muted">{countFor(c)}</span> : null}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${category?.key}`} aria-labelledby={`tab-${category?.key}`} className="pt-4">
        {load.state === "loading" ? (
          <div className="space-y-3 py-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : load.state === "error" ? (
          <ErrorState message={load.message} onRetry={() => setAttempt((n) => n + 1)} />
        ) : (
          <>
            <dl className="divide-y divide-line">
              {present.map((definition) => {
                const spec = byKey.get(definition.key)!;
                return (
                  <div key={definition.key} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 py-3">
                    <dt className="text-sm text-muted">{definition.name}</dt>
                    <dd className="tabular text-right text-sm">
                      {displayValue(spec)}
                      {spec.source.type === "legacy_v1" ? (
                        <span className="ml-2 text-xs text-warn">Unverified (legacy)</span>
                      ) : null}
                    </dd>
                    <SourceBadge spec={spec} />
                  </div>
                );
              })}
              {showMissing
                ? missing.map((definition) => (
                    <div key={definition.key} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 py-3">
                      <dt className="text-sm text-muted">{definition.name}</dt>
                      <dd className="text-right text-sm text-muted/60">Not specified</dd>
                      <span className="w-5" />
                    </div>
                  ))
                : null}
            </dl>
            {present.length === 0 && !showMissing ? (
              <p className="py-6 text-sm text-muted">Nothing in this section is recorded for this variant yet.</p>
            ) : null}
            {missing.length ? (
              <button onClick={() => setShowMissing((v) => !v)} className="mt-4 text-sm text-accent hover:underline">
                {showMissing ? "Hide" : "Show"} {missing.length} not specified
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

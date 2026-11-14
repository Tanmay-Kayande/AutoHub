"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, Skeleton } from "@/components/ui/States";
import { ApiError, request } from "@/lib/api";
import { formatPrice, label, modelHref } from "@/lib/format";
import type { CompareOut, ModelSummary, Page, VariantDetail } from "@/lib/types";

const MAX = 4;

function Picker({ models, onAdd, disabled }: { models: ModelSummary[]; onAdd: (id: number) => void; disabled: boolean }) {
  const [modelId, setModelId] = useState<number | null>(null);
  const [variants, setVariants] = useState<VariantDetail[]>([]);
  const [variantId, setVariantId] = useState<number | null>(null);

  useEffect(() => {
    setVariants([]);
    setVariantId(null);
    if (modelId === null) return;
    request<Page<VariantDetail>>("/variants", { query: { model_id: modelId, page_size: 100 } })
      .then((page) => {
        setVariants(page.items);
        setVariantId(page.items[0]?.id ?? null);
      })
      .catch(() => setVariants([]));
  }, [modelId]);

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 md:flex-row md:items-end">
      <label className="flex flex-1 flex-col gap-1 text-sm">
        <span className="text-xs uppercase tracking-[0.15em] text-muted">Model</span>
        <select
          value={modelId ?? ""}
          onChange={(e) => setModelId(e.target.value ? Number(e.target.value) : null)}
          className="rounded-xl border border-line bg-bg px-3 py-2.5"
        >
          <option value="">Choose a model…</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.brand.name} {m.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-1 flex-col gap-1 text-sm">
        <span className="text-xs uppercase tracking-[0.15em] text-muted">Variant</span>
        <select
          value={variantId ?? ""}
          onChange={(e) => setVariantId(Number(e.target.value))}
          disabled={!variants.length}
          className="rounded-xl border border-line bg-bg px-3 py-2.5 disabled:opacity-50"
        >
          {variants.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </label>
      <button
        onClick={() => variantId !== null && onAdd(variantId)}
        disabled={disabled || variantId === null}
        className="rounded-xl bg-accent px-5 py-2.5 text-sm font-medium text-bg disabled:opacity-40"
      >
        Add to compare
      </button>
    </div>
  );
}

export function Compare({ models, initialIds }: { models: ModelSummary[]; initialIds: number[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ids, setIds] = useState<number[]>(initialIds.slice(0, MAX));
  const [data, setData] = useState<CompareOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [onlyDiff, setOnlyDiff] = useState(false);

  useEffect(() => {
    router.replace(ids.length ? `${pathname}?variants=${ids.join(",")}` : pathname, { scroll: false });
    if (ids.length < 2) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    request<CompareOut>("/compare", { query: { variant_ids: ids.join(",") } })
      .then((out) => !cancelled && setData(out))
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : "The comparison could not be loaded."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ids, pathname, router]);

  const add = (id: number) => setIds((prev) => (prev.includes(id) || prev.length >= MAX ? prev : [...prev, id]));
  const remove = (id: number) => setIds((prev) => prev.filter((x) => x !== id));
  const order = data ? ids.map((id) => data.variants.find((v) => v.id === id)).filter(Boolean) as VariantDetail[] : [];

  return (
    <div className="space-y-8">
      <Picker models={models} onAdd={add} disabled={ids.length >= MAX} />
      <p className="text-sm text-muted">
        {ids.length}/{MAX} selected{ids.length < 2 ? " — add at least two variants." : ""}
      </p>

      {ids.length < 2 ? (
        <EmptyState title="Pick two to four variants" body="Choose a model, then a variant, and add it. The link to this page keeps your selection." />
      ) : error ? (
        <ErrorState message={error} onRetry={() => setIds((prev) => [...prev])} />
      ) : loading && !data ? (
        <Skeleton className="h-96 w-full" />
      ) : data ? (
        <div>
          <div className="mb-4 flex items-center justify-between gap-4">
            <p className="text-sm text-muted">
              {data.rows_differing} of {data.rows_total} specifications differ
            </p>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} className="accent-[var(--color-accent)]" />
              Only differences
            </label>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-line">
            <table className="w-full min-w-[640px] border-collapse text-sm">
              <thead>
                <tr className="bg-surface">
                  <th scope="col" className="sticky left-0 z-10 w-48 bg-surface p-4 text-left font-normal text-muted">
                    Specification
                  </th>
                  {order.map((v) => (
                    <th key={v.id} scope="col" className="p-4 text-left align-top font-normal">
                      <Link href={modelHref(v.brand.slug, v.model_slug)} className="text-xs uppercase tracking-[0.15em] text-muted hover:text-accent">
                        {v.brand.name} {v.model_name}
                      </Link>
                      <p className="mt-1 font-display text-base text-text">{v.name}</p>
                      <p className="mt-1 text-xs text-muted">
                        {[label(v.fuel_type), label(v.transmission_type)].filter(Boolean).join(" · ")}
                        {v.price_ex_showroom ? ` · ${formatPrice(v.price_ex_showroom)}` : ""}
                      </p>
                      <button onClick={() => remove(v.id)} className="mt-2 text-xs text-muted hover:text-warn">
                        Remove
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              {data.groups.map((group) => {
                const rows = onlyDiff ? group.rows.filter((r) => r.differs) : group.rows;
                if (!rows.length) return null;
                return (
                  <tbody key={group.key}>
                    <tr>
                      <th colSpan={order.length + 1} scope="colgroup" className="bg-bg px-4 pb-2 pt-6 text-left text-xs uppercase tracking-[0.2em] text-accent">
                        {group.name}
                      </th>
                    </tr>
                    {rows.map((row) => (
                      <tr key={row.key} className={`border-t border-line ${row.differs ? "bg-surface-2/40" : ""}`}>
                        <th scope="row" className="sticky left-0 bg-bg p-4 text-left font-normal text-muted">
                          {row.name}
                        </th>
                        {order.map((v) => {
                          const cell = row.cells.find((c) => c.variant_id === v.id);
                          const best = row.best_variant_ids.includes(v.id) && row.best_variant_ids.length < order.length;
                          return (
                            <td key={v.id} className="tabular p-4">
                              {cell && cell.status !== "missing" && cell.display ? (
                                <span className={best ? "text-good" : ""}>
                                  {cell.display}
                                  {best ? <span className="ml-2 text-[10px] uppercase tracking-wider">Best</span> : null}
                                  {cell.source_type === "legacy_v1" ? <span className="ml-2 text-[10px] text-warn">Unverified</span> : null}
                                </span>
                              ) : (
                                <span className="text-muted/60">Not specified</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                );
              })}
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

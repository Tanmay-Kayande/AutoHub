"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, Skeleton } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";
import { formatPrice, label, modelHref } from "@/lib/format";
import type {
  ModelSummary,
  SearchOut,
  SpecCategory,
  SpecDefinition,
  SpecFilter,
  StructuredOut,
  StructuredQuery,
} from "@/lib/types";

const BODY_TYPES = ["suv", "hatchback", "sedan", "mpv", "pickup"];
const FUEL_TYPES = ["petrol", "diesel", "cng", "hybrid", "electric"];
const TRANSMISSIONS = ["manual", "automatic", "amt", "cvt", "dct", "single_speed"];
const DRIVETRAINS = ["fwd", "rwd", "awd", "4wd"];
const LAKH = 100_000;

interface RangeFilter {
  key: string;
  min: string;
  max: string;
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function number(text: string): number | null {
  const value = Number.parseFloat(text);
  return Number.isFinite(value) ? value : null;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "The search could not be completed.";
}

function ChipGroup({
  legend,
  options,
  selected,
  onChange,
}: {
  legend: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-xs uppercase tracking-[0.15em] text-muted">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const active = selected.includes(option);
          return (
            <button
              key={option}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(toggle(selected, option))}
              className={`rounded-full border px-3 py-1 text-sm ${
                active ? "border-accent/60 bg-accent/10 text-accent" : "border-line text-muted hover:text-text"
              }`}
            >
              {label(option)}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function TextSearch({ modelsById }: { modelsById: Map<number, ModelSummary> }) {
  const router = useRouter();
  const params = useSearchParams();
  const initial = params.get("q") ?? "";
  const [query, setQuery] = useState(initial);
  const [result, setResult] = useState<SearchOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(text: string) {
    const q = text.trim();
    if (q.length < 2) return;
    setBusy(true);
    setError(null);
    router.replace(`/search?q=${encodeURIComponent(q)}`, { scroll: false });
    try {
      setResult(await api.search(q));
    } catch (e) {
      setError(errorMessage(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (initial) void run(initial);
    // Run once for a shared ?q= link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section aria-labelledby="text-search">
      <h2 id="text-search" className="font-display text-xl">
        Search brochures and specs
      </h2>
      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void run(query);
        }}
      >
        <label htmlFor="search-q" className="sr-only">
          Search
        </label>
        <input
          id="search-q"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={300}
          placeholder="e.g. sunroof, 360 camera, boot space, ground clearance"
          className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-2.5 text-sm focus:border-accent/60 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || query.trim().length < 2}
          className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-bg disabled:opacity-40"
        >
          {busy ? "…" : "Search"}
        </button>
      </form>
      <div className="mt-4" aria-live="polite">
        {busy ? <Skeleton className="h-24 w-full" /> : null}
        {error ? <ErrorState message={error} /> : null}
        {result && !busy ? (
          result.items.length === 0 ? (
            <EmptyState title="Nothing found" body="Try other words, or use the filters below." />
          ) : (
            <>
              {result.degraded ? (
                <p className="mb-2 text-xs text-muted">Showing keyword matches only (semantic search is unavailable).</p>
              ) : null}
              <ul className="divide-y divide-line rounded-2xl border border-line">
                {result.items.map((hit) => {
                  const model = hit.model_id ? modelsById.get(hit.model_id) : undefined;
                  const meta = hit.metadata as Record<string, string | undefined>;
                  const title = [meta.brand, meta.model, meta.variant].filter(Boolean).join(" ");
                  return (
                    <li key={`${hit.entity_type}-${hit.entity_id}-${hit.chunk_index}`} className="p-4 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">
                          {label(hit.entity_type)}
                        </span>
                        {model ? (
                          <Link href={modelHref(model.brand.slug, model.slug)} className="font-medium text-accent hover:underline">
                            {title || `${model.brand.name} ${model.name}`}
                          </Link>
                        ) : (
                          <span className="font-medium">{title}</span>
                        )}
                      </div>
                      <p className="mt-1 line-clamp-3 text-muted">{hit.content}</p>
                    </li>
                  );
                })}
              </ul>
            </>
          )
        ) : null}
      </div>
    </section>
  );
}

function FilterSearch({ models, categories }: { models: ModelSummary[]; categories: SpecCategory[] }) {
  const brands = useMemo(() => [...new Set(models.map((m) => m.brand.name))].sort(), [models]);
  const numeric = useMemo(
    () => categories.flatMap((c) => c.definitions).filter((d) => d.data_type === "number"),
    [categories],
  );
  const features = useMemo(
    () => categories.flatMap((c) => c.definitions).filter((d) => d.data_type === "boolean"),
    [categories],
  );
  const byKey = useMemo(() => new Map<string, SpecDefinition>(numeric.concat(features).map((d) => [d.key, d])), [numeric, features]);

  const [brand, setBrand] = useState<string[]>([]);
  const [body, setBody] = useState<string[]>([]);
  const [fuel, setFuel] = useState<string[]>([]);
  const [gearbox, setGearbox] = useState<string[]>([]);
  const [drive, setDrive] = useState<string[]>([]);
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");
  const [ranges, setRanges] = useState<RangeFilter[]>([]);
  const [mustHave, setMustHave] = useState<string[]>([]);
  const [sortKey, setSortKey] = useState("price");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [byModel, setByModel] = useState(false);
  const [result, setResult] = useState<StructuredOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function buildQuery(): StructuredQuery {
    const specFilters: SpecFilter[] = [];
    for (const r of ranges) {
      const min = number(r.min);
      const max = number(r.max);
      if (min !== null && max !== null) specFilters.push({ key: r.key, op: "between", value: min, value_max: max });
      else if (min !== null) specFilters.push({ key: r.key, op: "gte", value: min });
      else if (max !== null) specFilters.push({ key: r.key, op: "lte", value: max });
    }
    for (const key of mustHave) specFilters.push({ key, op: "is_true" });
    const min = number(priceMin);
    const max = number(priceMax);
    return {
      filters: {
        brand,
        body_type: body,
        fuel_type: fuel,
        transmission_type: gearbox,
        drivetrain: drive,
        price_min: min !== null ? min * LAKH : null,
        price_max: max !== null ? max * LAKH : null,
      },
      spec_filters: specFilters,
      sort: { key: sortKey, direction },
      group_by: byModel ? "model" : "variant",
      limit: 50,
    };
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.structured(buildQuery()));
    } catch (e) {
      setError(errorMessage(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const shownKeys = [...new Set([...ranges.map((r) => r.key), ...(sortKey !== "price" ? [sortKey] : []), ...mustHave])];
  const unused = numeric.filter((d) => !ranges.some((r) => r.key === d.key));
  const unit = (key: string) => byKey.get(key)?.canonical_unit ?? "";
  const inputClass =
    "w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm focus:border-accent/60 focus:outline-none";

  return (
    <section aria-labelledby="filters" className="mt-14">
      <h2 id="filters" className="font-display text-xl">
        Filter every variant
      </h2>
      <form
        className="mt-4 grid gap-6 rounded-2xl border border-line bg-surface/50 p-4 sm:p-6 lg:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <div className="space-y-5">
          <ChipGroup legend="Brand" options={brands} selected={brand} onChange={setBrand} />
          <ChipGroup legend="Body type" options={BODY_TYPES} selected={body} onChange={setBody} />
          <ChipGroup legend="Fuel" options={FUEL_TYPES} selected={fuel} onChange={setFuel} />
          <ChipGroup legend="Gearbox" options={TRANSMISSIONS} selected={gearbox} onChange={setGearbox} />
          <ChipGroup legend="Drive" options={DRIVETRAINS} selected={drive} onChange={setDrive} />
        </div>

        <div className="space-y-5">
          <fieldset>
            <legend className="mb-2 text-xs uppercase tracking-[0.15em] text-muted">Ex-showroom price (lakh)</legend>
            <div className="flex items-center gap-2">
              <label className="sr-only" htmlFor="price-min">
                Minimum price in lakh
              </label>
              <input id="price-min" inputMode="decimal" placeholder="min" value={priceMin} onChange={(e) => setPriceMin(e.target.value)} className={inputClass} />
              <span className="text-muted">–</span>
              <label className="sr-only" htmlFor="price-max">
                Maximum price in lakh
              </label>
              <input id="price-max" inputMode="decimal" placeholder="max" value={priceMax} onChange={(e) => setPriceMax(e.target.value)} className={inputClass} />
            </div>
            <p className="mt-1 text-xs text-muted">Only variants with a published price match a price filter.</p>
          </fieldset>

          <fieldset>
            <legend className="mb-2 text-xs uppercase tracking-[0.15em] text-muted">Specifications</legend>
            <ul className="space-y-2">
              {ranges.map((r, index) => (
                <li key={r.key} className="grid grid-cols-[1fr_5rem_5rem_auto] items-center gap-2 text-sm">
                  <span className="truncate">
                    {byKey.get(r.key)?.name} {unit(r.key) ? <span className="text-muted">({unit(r.key)})</span> : null}
                  </span>
                  <input
                    aria-label={`${byKey.get(r.key)?.name} minimum`}
                    inputMode="decimal"
                    placeholder="min"
                    value={r.min}
                    onChange={(e) => setRanges((all) => all.map((x, i) => (i === index ? { ...x, min: e.target.value } : x)))}
                    className={inputClass}
                  />
                  <input
                    aria-label={`${byKey.get(r.key)?.name} maximum`}
                    inputMode="decimal"
                    placeholder="max"
                    value={r.max}
                    onChange={(e) => setRanges((all) => all.map((x, i) => (i === index ? { ...x, max: e.target.value } : x)))}
                    className={inputClass}
                  />
                  <button
                    type="button"
                    aria-label={`Remove ${byKey.get(r.key)?.name}`}
                    onClick={() => setRanges((all) => all.filter((_, i) => i !== index))}
                    className="px-1 text-muted hover:text-text"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
            <label htmlFor="add-range" className="sr-only">
              Add a specification filter
            </label>
            <select
              id="add-range"
              value=""
              onChange={(e) => e.target.value && setRanges((all) => [...all, { key: e.target.value, min: "", max: "" }])}
              className={`${inputClass} mt-2`}
            >
              <option value="">+ Add a specification range…</option>
              {unused.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.name}
                </option>
              ))}
            </select>
          </fieldset>

          <fieldset>
            <legend className="mb-2 text-xs uppercase tracking-[0.15em] text-muted">Must have</legend>
            <div className="flex flex-wrap gap-2">
              {mustHave.map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setMustHave((all) => all.filter((k) => k !== key))}
                  className="rounded-full border border-accent/60 bg-accent/10 px-3 py-1 text-sm text-accent"
                  aria-label={`Remove ${byKey.get(key)?.name}`}
                >
                  {byKey.get(key)?.name} ×
                </button>
              ))}
            </div>
            <label htmlFor="add-feature" className="sr-only">
              Add a required feature
            </label>
            <select
              id="add-feature"
              value=""
              onChange={(e) => e.target.value && setMustHave((all) => [...all, e.target.value])}
              className={`${inputClass} mt-2`}
            >
              <option value="">+ Add a feature…</option>
              {features
                .filter((d) => !mustHave.includes(d.key))
                .map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.name}
                  </option>
                ))}
            </select>
          </fieldset>

          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[12rem] flex-1">
              <label htmlFor="sort" className="mb-2 block text-xs uppercase tracking-[0.15em] text-muted">
                Sort by
              </label>
              <select id="sort" value={sortKey} onChange={(e) => setSortKey(e.target.value)} className={inputClass}>
                <option value="price">Price</option>
                {numeric.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
            <label htmlFor="direction" className="sr-only">
              Sort direction
            </label>
            <select
              id="direction"
              value={direction}
              onChange={(e) => setDirection(e.target.value as "asc" | "desc")}
              className={`${inputClass} w-auto`}
            >
              <option value="asc">Lowest first</option>
              <option value="desc">Highest first</option>
            </select>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={byModel} onChange={(e) => setByModel(e.target.checked)} />
              One row per model
            </label>
          </div>
        </div>

        <div className="lg:col-span-2">
          <button type="submit" disabled={busy} className="rounded-xl bg-accent px-5 py-2 text-sm font-medium text-bg disabled:opacity-40">
            {busy ? "Searching…" : "Show matching cars"}
          </button>
        </div>
      </form>

      <div className="mt-6" aria-live="polite">
        {error ? <ErrorState message={error} /> : null}
        {result && !busy ? (
          result.items.length === 0 ? (
            <EmptyState title="No car matches every filter" body="Remove a filter or widen a range." />
          ) : (
            <>
              <p className="mb-3 text-sm text-muted">
                {result.total_matched} {byModel ? "models" : "variants"} match
                {result.total_matched > result.items.length ? `; showing the first ${result.items.length}` : ""}.
                {Object.entries(result.missing_value_counts)
                  .filter(([, n]) => n > 0)
                  .map(([key, n]) => ` ${n} left out for having no ${byKey.get(key)?.name?.toLowerCase() ?? key} value.`)
                  .join("")}
              </p>
              <div className="overflow-x-auto rounded-2xl border border-line">
                <table className="w-full min-w-[40rem] text-left text-sm">
                  <thead className="bg-surface-2 text-xs uppercase tracking-[0.1em] text-muted">
                    <tr>
                      <th scope="col" className="p-3">Car</th>
                      <th scope="col" className="p-3">Fuel · gearbox</th>
                      <th scope="col" className="p-3">Price</th>
                      {shownKeys.map((key) => (
                        <th key={key} scope="col" className="p-3">
                          {byKey.get(key)?.name ?? key}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {result.items.map((row) => (
                      <tr key={row.variant_id}>
                        <td className="p-3">
                          <Link href={modelHref(row.brand_slug, row.model_slug)} className="font-medium text-accent hover:underline">
                            {row.brand} {row.model}
                          </Link>
                          {byModel ? null : <div className="text-xs text-muted">{row.variant}</div>}
                        </td>
                        <td className="p-3 text-muted">
                          {[label(row.fuel_type), label(row.transmission_type)].filter(Boolean).join(" · ")}
                        </td>
                        <td className="tabular p-3">{formatPrice(row.price_ex_showroom) ?? <span className="text-muted">—</span>}</td>
                        {shownKeys.map((key) => (
                          <td key={key} className="tabular p-3">
                            {row.values[key]?.display ?? <span className="text-muted">—</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        ) : null}
      </div>
    </section>
  );
}

export function Search({ models, categories }: { models: ModelSummary[]; categories: SpecCategory[] }) {
  const modelsById = useMemo(() => new Map(models.map((m) => [m.id, m])), [models]);
  return (
    <>
      <TextSearch modelsById={modelsById} />
      <FilterSearch models={models} categories={categories} />
    </>
  );
}

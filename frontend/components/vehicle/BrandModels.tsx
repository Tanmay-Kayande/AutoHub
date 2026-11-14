"use client";

import { useMemo, useState } from "react";

import { EmptyState } from "@/components/ui/States";
import { VehicleCard } from "@/components/vehicle/VehicleCard";
import { label } from "@/lib/format";
import type { ModelSummary } from "@/lib/types";

function FilterGroup({
  name,
  options,
  value,
  onChange,
}: {
  name: string;
  options: string[];
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  if (options.length < 2) return null;
  return (
    <div role="radiogroup" aria-label={name} className="flex flex-wrap items-center gap-2">
      <span className="mr-1 text-xs uppercase tracking-[0.15em] text-muted">{name}</span>
      {[null, ...options].map((option) => (
        <button
          key={option ?? "all"}
          role="radio"
          aria-checked={value === option}
          onClick={() => onChange(option)}
          className={`rounded-full border px-3 py-1 text-xs ${
            value === option ? "border-accent/60 text-accent" : "border-line text-muted hover:text-text"
          }`}
        >
          {option ? label(option) : "All"}
        </button>
      ))}
    </div>
  );
}

export function BrandModels({ models }: { models: ModelSummary[] }) {
  const [body, setBody] = useState<string | null>(null);
  const [fuel, setFuel] = useState<string | null>(null);
  const bodies = useMemo(() => [...new Set(models.map((m) => m.body_type).filter(Boolean))] as string[], [models]);
  const fuels = useMemo(() => [...new Set(models.flatMap((m) => m.fuel_types))].sort(), [models]);
  const shown = models.filter((m) => (!body || m.body_type === body) && (!fuel || m.fuel_types.includes(fuel)));

  return (
    <section className="mt-12">
      <div className="mb-8 flex flex-col gap-3">
        <FilterGroup name="Body" options={bodies} value={body} onChange={setBody} />
        <FilterGroup name="Fuel" options={fuels} value={fuel} onChange={setFuel} />
      </div>
      {shown.length === 0 ? (
        <EmptyState title="No models match these filters" body="Clear a filter to see more models." />
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((model) => (
            <VehicleCard key={model.id} model={model} />
          ))}
        </div>
      )}
    </section>
  );
}

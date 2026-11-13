import Link from "next/link";

import { SmartImage } from "@/components/ui/SmartImage";
import { label, modelHref, priceRange } from "@/lib/format";
import type { ModelSummary } from "@/lib/types";

export function VehicleCard({ model }: { model: ModelSummary }) {
  const name = `${model.brand.name} ${model.name}`;
  const price = priceRange(model.price_min, model.price_max);
  return (
    <Link
      href={modelHref(model.brand.slug, model.slug)}
      className="group flex flex-col overflow-hidden rounded-2xl border border-line bg-surface transition-colors hover:border-accent/30 hover:bg-surface-2"
    >
      <SmartImage
        key={model.primary_image?.image_url ?? model.id}
        src={model.primary_image?.image_url}
        alt={`${name} — exterior`}
        label={name}
        className="aspect-[16/10] w-full"
      />
      <div className="flex flex-1 flex-col gap-3 p-5">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-muted">{model.brand.name}</p>
          <h3 className="font-display text-xl font-semibold tracking-tight group-hover:text-accent">{model.name}</h3>
        </div>
        <p className="text-sm text-muted">
          {[label(model.body_type), model.fuel_types.map(label).join(" · ")].filter(Boolean).join(" — ") ||
            "Details being collected"}
        </p>
        <div className="mt-auto flex items-end justify-between gap-2 pt-2">
          <span className="text-sm">{price ?? <span className="text-muted">Price not published</span>}</span>
          <span className="text-xs text-muted">
            {model.variant_count} variant{model.variant_count === 1 ? "" : "s"}
          </span>
        </div>
      </div>
    </Link>
  );
}

import type { Metadata } from "next";
import Link from "next/link";

import { EmptyState, ErrorState, SectionHeading } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";

// Rendered per request (data is cached per fetch for 60 s); never baked at build time.
export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Brands" };

export default async function BrandsPage() {
  let brands;
  try {
    brands = await api.brands();
  } catch (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message={error instanceof ApiError ? error.message : "Brands could not be loaded."} />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
      <SectionHeading as="h1" eyebrow="Catalogue" title="Brands" />
      {brands.items.length === 0 ? (
        <EmptyState title="No brands yet" body="Brands appear once their catalogue has been imported." />
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {brands.items.map((brand) => (
            <Link
              key={brand.id}
              href={`/brands/${brand.slug}`}
              className="group rounded-3xl border border-line bg-surface p-8 transition-colors hover:border-accent/30 hover:bg-surface-2"
            >
              <p className="text-xs uppercase tracking-[0.2em] text-muted">{brand.country ?? "—"}</p>
              <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight group-hover:text-accent">
                {brand.name}
              </h2>
              <p className="mt-6 text-sm text-muted">
                {brand.model_count ?? 0} model{brand.model_count === 1 ? "" : "s"}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

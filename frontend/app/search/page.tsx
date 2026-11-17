import type { Metadata } from "next";
import { Suspense } from "react";

import { Search } from "@/components/search/Search";
import { ErrorState } from "@/components/ui/States";
import { api } from "@/lib/api";

export const metadata: Metadata = {
  title: "Search and filter",
  description: "Search brochures and specifications, or filter every variant by fuel, gearbox, price and specs.",
};

export default async function SearchPage() {
  const [models, definitions] = await Promise.all([
    api.models({ page_size: 100 }).catch(() => null),
    api.specDefinitions().catch(() => null),
  ]);
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">Search</p>
      <h1 className="mb-10 mt-2 font-display text-4xl font-semibold tracking-tight">Find the right car</h1>
      {models && definitions ? (
        <Suspense>
          <Search models={models.items} categories={definitions} />
        </Suspense>
      ) : (
        <ErrorState message="The catalogue could not be loaded." />
      )}
    </div>
  );
}

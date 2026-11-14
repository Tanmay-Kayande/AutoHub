import type { Metadata } from "next";
import { Suspense } from "react";

import { Compare } from "@/components/compare/Compare";
import { ErrorState } from "@/components/ui/States";
import { api } from "@/lib/api";

export const metadata: Metadata = { title: "Compare" };

type Search = Promise<{ variants?: string }>;

export default async function ComparePage({ searchParams }: { searchParams: Search }) {
  const { variants } = await searchParams;
  const ids = (variants ?? "")
    .split(",")
    .map((s) => Number.parseInt(s, 10))
    .filter((n) => Number.isInteger(n) && n > 0);
  const models = await api.models({ page_size: 100 }).catch(() => null);
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">Compare</p>
      <h1 className="mb-10 mt-2 font-display text-4xl font-semibold tracking-tight">Side by side</h1>
      {models ? (
        <Suspense>
          <Compare models={models.items} initialIds={[...new Set(ids)]} />
        </Suspense>
      ) : (
        <ErrorState message="The model list could not be loaded." />
      )}
    </div>
  );
}

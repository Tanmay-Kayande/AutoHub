import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { BrandModels } from "@/components/vehicle/BrandModels";
import { ErrorState } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";

type Params = Promise<{ brand: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { brand } = await params;
  try {
    return { title: (await api.brand(brand)).name };
  } catch {
    return { title: "Brand" };
  }
}

export default async function BrandPage({ params }: { params: Params }) {
  const { brand: slug } = await params;
  let brand;
  try {
    brand = await api.brand(slug);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message={error instanceof ApiError ? error.message : "This brand could not be loaded."} />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">{brand.country ?? "Brand"}</p>
      <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight sm:text-5xl">{brand.name}</h1>
      {brand.description ? <p className="mt-4 max-w-2xl text-muted">{brand.description}</p> : null}
      {brand.website_url ? (
        <a href={brand.website_url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm text-accent hover:underline">
          Official website ↗
        </a>
      ) : null}
      <BrandModels models={brand.models} />
    </div>
  );
}

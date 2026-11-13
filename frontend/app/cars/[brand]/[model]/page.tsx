import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Chat } from "@/components/assistant/Chat";
import { ScrollGallery } from "@/components/gallery/ScrollGallery";
import { ErrorState } from "@/components/ui/States";
import { SmartImage } from "@/components/ui/SmartImage";
import { VariantSpecs } from "@/components/vehicle/VariantSpecs";
import { api, ApiError } from "@/lib/api";
import { label, priceRange, sourceLabel } from "@/lib/format";
import type { VehicleImage } from "@/lib/types";

type Params = Promise<{ brand: string; model: string }>;

const GALLERIES: { category: string; title: string }[] = [
  { category: "exterior", title: "Exterior" },
  { category: "interior", title: "Interior" },
  { category: "dashboard", title: "Dashboard" },
  { category: "rear_seat", title: "Rear seat" },
  { category: "boot", title: "Boot" },
];

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { brand, model } = await params;
  try {
    const detail = await api.model(brand, model);
    return { title: `${detail.brand.name} ${detail.name}`, description: detail.description ?? undefined };
  } catch {
    return { title: "Car" };
  }
}

export default async function ModelPage({ params }: { params: Params }) {
  const { brand, model: slug } = await params;
  let model;
  try {
    model = await api.model(brand, slug);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message={error instanceof ApiError ? error.message : "This model could not be loaded."} />
      </div>
    );
  }
  const [images, categories, suggestions] = await Promise.all([
    api.images(model.id).catch(() => [] as VehicleImage[]),
    api.specDefinitions().catch(() => []),
    api.suggestions(model.id).catch(() => [] as string[]),
  ]);
  const name = `${model.brand.name} ${model.name}`;
  const current = model.generations.find((g) => g.is_current) ?? model.generations[0];
  const variants = current?.variants ?? [];
  const price = priceRange(model.price_min, model.price_max);
  const byCategory = (category: string) => images.filter((image) => image.category === category);

  return (
    <article>
      <section className="relative isolate overflow-hidden">
        <SmartImage
          src={model.primary_image?.image_url}
          alt={`${name} — exterior`}
          label={name}
          className="absolute inset-0 -z-10 h-full w-full opacity-60"
          eager
        />
        <div className="absolute inset-0 -z-10 bg-gradient-to-t from-bg via-bg/50 to-transparent" />
        <div className="mx-auto flex min-h-[70vh] max-w-7xl flex-col justify-end px-4 pb-12 sm:px-6">
          <Link href={`/brands/${model.brand.slug}`} className="text-xs uppercase tracking-[0.25em] text-accent hover:underline">
            {model.brand.name}
          </Link>
          <h1 className="mt-2 font-display text-5xl font-semibold tracking-tight sm:text-7xl">{model.name}</h1>
          <p className="mt-4 text-muted">
            {[label(model.body_type), model.fuel_types.map(label).join(" · "), model.transmission_types.map(label).join(" · ")]
              .filter(Boolean)
              .join(" — ")}
          </p>
          <p className="mt-2 text-lg">{price ?? <span className="text-muted">Ex-showroom price not published</span>}</p>
        </div>
      </section>

      {model.key_specs.length ? (
        <section className="border-y border-line bg-surface/60" aria-label="Key specifications">
          <dl className="mx-auto grid max-w-7xl grid-cols-2 gap-px px-4 sm:px-6 md:grid-cols-3 lg:grid-cols-6">
            {model.key_specs.map((spec) => (
              <div key={spec.key} className="py-6 pr-4">
                <dt className="text-xs uppercase tracking-[0.15em] text-muted">{spec.name}</dt>
                <dd className="tabular mt-1 font-display text-xl text-accent">{spec.display}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <div className="space-y-24 pt-16">
        {GALLERIES.map(({ category, title }) =>
          byCategory(category).length ? (
            <ScrollGallery key={category} images={byCategory(category)} title={title} alt={name} />
          ) : null,
        )}
        {images.length === 0 ? (
          <p className="mx-auto max-w-7xl px-4 text-sm text-muted sm:px-6">No photos have been collected for this model yet.</p>
        ) : null}

        <section className="mx-auto max-w-7xl px-4 sm:px-6" aria-labelledby="specs-heading">
          <p className="text-xs uppercase tracking-[0.2em] text-accent">Specifications</p>
          <h2 id="specs-heading" className="mb-8 mt-1 font-display text-3xl font-semibold tracking-tight">
            {variants.length} variant{variants.length === 1 ? "" : "s"}
            {current && model.generations.length > 1 ? <span className="text-muted"> · {current.name}</span> : null}
          </h2>
          <VariantSpecs variants={variants} categories={categories} />
        </section>

        <section className="mx-auto grid max-w-7xl gap-10 px-4 sm:px-6 lg:grid-cols-[1fr_1.4fr]" aria-labelledby="ask-heading">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-accent">Ask AutoHub</p>
            <h2 id="ask-heading" className="mt-1 font-display text-3xl font-semibold tracking-tight">
              Questions about the {model.name}?
            </h2>
            <p className="mt-3 text-sm text-muted">
              Answers use this model&apos;s recorded data and brochure text, and show the evidence for every fact.
            </p>
            <div className="mt-10">
              <h3 className="font-display text-lg">Sources</h3>
              <ul className="mt-3 space-y-1 text-sm text-muted">
                {model.data_sources.length ? (
                  model.data_sources.map((s) => <li key={s}>{sourceLabel(s)}</li>)
                ) : (
                  <li>No specification sources recorded yet.</li>
                )}
                <li>
                  {model.brochure_count} official brochure{model.brochure_count === 1 ? "" : "s"} on file
                </li>
              </ul>
            </div>
          </div>
          <div className="rounded-3xl border border-line bg-surface p-5">
            <Chat modelId={model.id} suggestions={suggestions} compact />
          </div>
        </section>
      </div>
    </article>
  );
}

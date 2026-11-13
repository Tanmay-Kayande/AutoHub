import Link from "next/link";

import { AskBar } from "@/components/home/AskBar";
import { ErrorState, SectionHeading } from "@/components/ui/States";
import { SmartImage } from "@/components/ui/SmartImage";
import { VehicleCard } from "@/components/vehicle/VehicleCard";
import { api, ApiError } from "@/lib/api";
import { formatDate, modelHref } from "@/lib/format";
import type { Brand, ModelSummary, News, Page } from "@/lib/types";

// Rendered per request (data is cached per fetch for 60 s); never baked at build time.
export const dynamic = "force-dynamic";

async function load(): Promise<{ models: Page<ModelSummary>; brands: Page<Brand>; news: Page<News> }> {
  const [models, brands, news] = await Promise.all([api.models({ page_size: 50 }), api.brands(), api.news()]);
  return { models, brands, news };
}

export default async function HomePage() {
  let data: Awaited<ReturnType<typeof load>>;
  try {
    data = await load();
  } catch (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message={error instanceof ApiError ? error.message : "The catalogue could not be loaded."} />
      </div>
    );
  }
  const withImages = data.models.items.filter((m) => m.primary_image);
  const featured = withImages[0] ?? data.models.items[0];
  const grid = [...withImages, ...data.models.items.filter((m) => !m.primary_image)].slice(0, 6);

  return (
    <>
      <section className="relative isolate min-h-[78vh] overflow-hidden">
        {featured ? (
          <SmartImage
            src={featured.primary_image?.image_url}
            alt={`${featured.brand.name} ${featured.name}`}
            className="absolute inset-0 -z-10 h-full w-full opacity-55"
            eager
          />
        ) : null}
        <div className="absolute inset-0 -z-10 bg-gradient-to-t from-bg via-bg/60 to-bg/20" />
        <div className="mx-auto flex min-h-[78vh] max-w-7xl flex-col justify-end gap-8 px-4 pb-16 sm:px-6">
          <div className="max-w-3xl">
            <p className="text-xs uppercase tracking-[0.25em] text-accent">Every spec, from the source</p>
            <h1 className="mt-4 font-display text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
              Find the right car with facts you can check.
            </h1>
            <p className="mt-5 max-w-xl text-muted">
              {data.models.total} models from {data.brands.total} brands, with specifications read from official
              brochures — and an assistant that shows where every answer comes from.
            </p>
          </div>
          <AskBar />
          {featured ? (
            <Link
              href={modelHref(featured.brand.slug, featured.slug)}
              className="w-fit text-sm text-muted hover:text-text"
            >
              Pictured: {featured.brand.name} {featured.name} →
            </Link>
          ) : null}
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 pt-20 sm:px-6">
        <SectionHeading eyebrow="Catalogue" title="Explore models">
          <Link href="/brands" className="text-sm text-accent hover:underline">
            All brands →
          </Link>
        </SectionHeading>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {grid.map((model) => (
            <VehicleCard key={model.id} model={model} />
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 pt-24 sm:px-6">
        <SectionHeading eyebrow="Brands" title="Browse by maker" />
        <div className="flex flex-wrap gap-3">
          {data.brands.items.map((brand) => (
            <Link
              key={brand.id}
              href={`/brands/${brand.slug}`}
              className="rounded-2xl border border-line bg-surface px-6 py-4 hover:border-accent/30"
            >
              <span className="font-display text-lg">{brand.name}</span>
              <span className="ml-3 text-sm text-muted">{brand.model_count ?? 0} models</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-6 px-4 pt-24 sm:px-6 md:grid-cols-2">
        <Link href="/compare" className="rounded-3xl border border-line bg-surface p-8 hover:bg-surface-2">
          <p className="text-xs uppercase tracking-[0.2em] text-accent">Compare</p>
          <h2 className="mt-3 font-display text-2xl font-semibold">Side by side, differences first</h2>
          <p className="mt-3 text-sm text-muted">
            Pick up to four variants. AutoHub highlights what differs and marks the better figure — and says so when
            a value is not specified.
          </p>
        </Link>
        <Link href="/ask" className="rounded-3xl border border-line bg-surface p-8 hover:bg-surface-2">
          <p className="text-xs uppercase tracking-[0.2em] text-accent">Ask AutoHub</p>
          <h2 className="mt-3 font-display text-2xl font-semibold">Answers with their evidence</h2>
          <p className="mt-3 text-sm text-muted">
            Every fact in an answer cites the brochure or record it came from. When AutoHub does not know, it says
            so.
          </p>
        </Link>
      </section>

      <section className="mx-auto max-w-7xl px-4 pt-24 sm:px-6">
        <SectionHeading eyebrow="News" title="Latest">
          <Link href="/news" className="text-sm text-accent hover:underline">
            All news →
          </Link>
        </SectionHeading>
        {data.news.items.length ? (
          <ul className="grid gap-4 md:grid-cols-3">
            {data.news.items.slice(0, 3).map((item) => (
              <li key={item.id}>
                <Link href={`/news/${item.id}`} className="block rounded-2xl border border-line bg-surface p-5 hover:bg-surface-2">
                  <p className="text-xs text-muted">{formatDate(item.published_at)}</p>
                  <p className="mt-2 font-display">{item.title}</p>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No news yet — articles will appear here as they are published.</p>
        )}
      </section>
    </>
  );
}

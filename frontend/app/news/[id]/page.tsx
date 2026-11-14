import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ErrorState } from "@/components/ui/States";
import { SmartImage } from "@/components/ui/SmartImage";
import { api, ApiError } from "@/lib/api";
import { formatDate, modelHref } from "@/lib/format";

type Params = Promise<{ id: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { id } = await params;
  try {
    return { title: (await api.newsItem(Number(id))).title };
  } catch {
    return { title: "News" };
  }
}

export default async function NewsItemPage({ params }: { params: Params }) {
  const { id } = await params;
  const numeric = Number.parseInt(id, 10);
  if (!Number.isInteger(numeric) || numeric < 1) notFound();
  let item;
  try {
    item = await api.newsItem(numeric);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message={error instanceof ApiError ? error.message : "This article could not be loaded."} />
      </div>
    );
  }
  return (
    <article className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <Link href="/news" className="text-sm text-accent hover:underline">
        ← All news
      </Link>
      <p className="mt-6 text-xs text-muted">
        {formatDate(item.published_at)}
        {item.source_name ? ` · ${item.source_name}` : ""}
      </p>
      <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">{item.title}</h1>
      {item.news_images[0] ? (
        <SmartImage src={item.news_images[0].image_url} alt={item.title} className="mt-8 aspect-[16/9] w-full rounded-2xl" />
      ) : null}
      <div className="mt-8 whitespace-pre-line leading-relaxed text-text/90">{item.content}</div>
      {item.source_url ? (
        <a href={item.source_url} target="_blank" rel="noreferrer" className="mt-6 inline-block text-sm text-accent hover:underline">
          Read at the source ↗
        </a>
      ) : null}
      {item.vehicles.length ? (
        <section className="mt-12 border-t border-line pt-8">
          <h2 className="font-display text-lg">Cars in this story</h2>
          <ul className="mt-4 flex flex-wrap gap-3">
            {item.vehicles.map((v) => (
              <li key={v.id}>
                <Link href={modelHref(v.brand.slug, v.slug)} className="rounded-full border border-line px-4 py-2 text-sm hover:border-accent/40">
                  {v.brand.name} {v.name}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}

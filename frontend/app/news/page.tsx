import type { Metadata } from "next";
import Link from "next/link";

import { EmptyState, ErrorState } from "@/components/ui/States";
import { SmartImage } from "@/components/ui/SmartImage";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";

// Rendered per request (data is cached per fetch for 60 s); never baked at build time.
export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "News" };

export default async function NewsPage() {
  let news;
  try {
    news = await api.news();
  } catch (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-24">
        <ErrorState message={error instanceof ApiError ? error.message : "News could not be loaded."} />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">News</p>
      <h1 className="mb-10 mt-2 font-display text-4xl font-semibold tracking-tight">Latest from the industry</h1>
      {news.items.length === 0 ? (
        <EmptyState
          title="No news yet"
          body="Articles about launches and updates will appear here, linked to the cars they mention. Meanwhile, explore the catalogue."
          action={{ href: "/brands", label: "Browse brands →" }}
        />
      ) : (
        <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {news.items.map((item) => (
            <li key={item.id}>
              <Link href={`/news/${item.id}`} className="group block overflow-hidden rounded-2xl border border-line bg-surface hover:bg-surface-2">
                <SmartImage src={item.news_images[0]?.image_url} alt={item.title} className="aspect-[16/9] w-full" />
                <div className="p-5">
                  <p className="text-xs text-muted">
                    {formatDate(item.published_at)}
                    {item.source_name ? ` · ${item.source_name}` : ""}
                  </p>
                  <h2 className="mt-2 font-display text-lg group-hover:text-accent">{item.title}</h2>
                  {item.summary ? <p className="mt-2 line-clamp-3 text-sm text-muted">{item.summary}</p> : null}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

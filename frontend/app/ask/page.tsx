import type { Metadata } from "next";

import { Chat } from "@/components/assistant/Chat";
import { api } from "@/lib/api";

export const metadata: Metadata = { title: "Ask AutoHub" };

type Search = Promise<{ q?: string }>;

export default async function AskPage({ searchParams }: { searchParams: Search }) {
  const { q } = await searchParams;
  const suggestions = await api.suggestions().catch(() => [] as string[]);
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">Ask AutoHub</p>
      <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">Ask about any car in the catalogue</h1>
      <p className="mt-3 text-sm text-muted">
        Facts come from official brochures and AutoHub&apos;s records, with the evidence shown. Explanations written by
        the assistant are labelled as AutoHub&apos;s take.
      </p>
      <div className="mt-10">
        <Chat suggestions={suggestions} initialQuestion={q?.slice(0, 1000)} />
      </div>
    </div>
  );
}

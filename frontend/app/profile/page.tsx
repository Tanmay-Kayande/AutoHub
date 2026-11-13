"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/States";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import type { Conversation } from "@/lib/types";

export default function ProfilePage() {
  const { user, token, ready, logout } = useAuth();
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[] | null>(null);

  useEffect(() => {
    if (ready && !user) router.replace("/login?next=/profile");
  }, [ready, user, router]);

  useEffect(() => {
    if (!token) return;
    api
      .conversations(token)
      .then(setConversations)
      .catch(() => setConversations([]));
  }, [token]);

  if (!ready || !user) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-20">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <h1 className="font-display text-3xl font-semibold tracking-tight">{user.name}</h1>
      <dl className="mt-6 grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
        <dt className="text-muted">Email</dt>
        <dd>{user.email}</dd>
        {user.location ? (
          <>
            <dt className="text-muted">City</dt>
            <dd>{user.location}</dd>
          </>
        ) : null}
        <dt className="text-muted">Member since</dt>
        <dd>{formatDate(user.created_at)}</dd>
      </dl>
      <button
        onClick={() => {
          logout();
          router.push("/");
        }}
        className="mt-6 rounded-full border border-line px-4 py-1.5 text-sm hover:bg-surface-2"
      >
        Log out
      </button>

      <section className="mt-14">
        <h2 className="font-display text-xl">Your questions</h2>
        {conversations === null ? (
          <Skeleton className="mt-4 h-24 w-full" />
        ) : conversations.length === 0 ? (
          <p className="mt-3 text-sm text-muted">
            No conversations yet.{" "}
            <Link href="/ask" className="text-accent hover:underline">
              Ask AutoHub something
            </Link>
            .
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-line rounded-2xl border border-line">
            {conversations.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-4 p-4 text-sm">
                <span className="truncate">{c.title ?? "Conversation"}</span>
                <span className="shrink-0 text-xs text-muted">{formatDate(c.updated_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

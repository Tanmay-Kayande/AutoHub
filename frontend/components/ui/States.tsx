"use client";

import Link from "next/link";

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-surface-2 ${className}`} aria-hidden />;
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: string;
  action?: { href: string; label: string };
}) {
  return (
    <div className="rounded-2xl border border-dashed border-line px-6 py-14 text-center">
      <p className="font-display text-lg">{title}</p>
      {body ? <p className="mx-auto mt-2 max-w-md text-sm text-muted">{body}</p> : null}
      {action ? (
        <Link href={action.href} className="mt-5 inline-block text-sm text-accent hover:underline">
          {action.label}
        </Link>
      ) : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-2xl border border-warn/30 bg-warn/5 px-6 py-8 text-center">
      <p className="font-display text-lg">Something went wrong</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted">{message}</p>
      {onRetry ? (
        <button onClick={onRetry} className="mt-5 rounded-full border border-line px-4 py-1.5 text-sm hover:bg-surface-2">
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  children,
  as: Heading = "h2",
}: {
  eyebrow?: string;
  title: string;
  children?: React.ReactNode;
  as?: "h1" | "h2";
}) {
  return (
    <div className="mb-8 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
      <div>
        {eyebrow ? <p className="text-xs uppercase tracking-[0.2em] text-accent">{eyebrow}</p> : null}
        <Heading className="mt-1 font-display text-2xl font-semibold tracking-tight md:text-3xl">{title}</Heading>
      </div>
      {children}
    </div>
  );
}

export function Chip({ children, active = false }: { children: React.ReactNode; active?: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs ${
        active ? "border-accent/50 text-accent" : "border-line text-muted"
      }`}
    >
      {children}
    </span>
  );
}

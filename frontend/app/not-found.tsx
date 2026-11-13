import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-4 py-32 text-center">
      <p className="text-xs uppercase tracking-[0.25em] text-accent">404</p>
      <h1 className="mt-3 font-display text-4xl font-semibold">This page isn&apos;t in the catalogue</h1>
      <p className="mt-3 text-muted">The car or page may have moved, or the link is mistyped.</p>
      <Link href="/brands" className="mt-8 inline-block rounded-full bg-accent px-5 py-2 text-sm font-medium text-bg">
        Browse brands
      </Link>
    </div>
  );
}

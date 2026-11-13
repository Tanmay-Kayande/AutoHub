import Link from "next/link";

export function Footer() {
  return (
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-10 text-sm text-muted sm:px-6 md:flex-row md:justify-between">
        <p>
          <span className="font-display text-text">AutoHub</span> · Specifications from official brochures, with
          their sources.
        </p>
        <p className="max-w-xl">
          Images are found by search and belong to their owners; the source of each is shown. Values marked
          &ldquo;Unverified (legacy)&rdquo; come from AutoHub&apos;s earlier data and are not yet confirmed by a
          brochure.
        </p>
        <nav className="flex gap-4" aria-label="Footer">
          <Link href="/brands">Brands</Link>
          <Link href="/compare">Compare</Link>
          <Link href="/ask">Ask</Link>
        </nav>
      </div>
    </footer>
  );
}

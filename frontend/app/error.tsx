"use client";

import { ErrorState } from "@/components/ui/States";

export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-24">
      <ErrorState message="This page failed to load. The API may be unavailable." onRetry={reset} />
    </div>
  );
}

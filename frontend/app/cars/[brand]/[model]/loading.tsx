import { Skeleton } from "@/components/ui/States";

export default function Loading() {
  return (
    <div>
      <Skeleton className="h-[70vh] w-full rounded-none" />
      <div className="mx-auto grid max-w-7xl grid-cols-2 gap-6 px-4 py-8 sm:px-6 md:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>
      <div className="mx-auto max-w-7xl space-y-3 px-4 sm:px-6">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-9" />
        ))}
      </div>
    </div>
  );
}

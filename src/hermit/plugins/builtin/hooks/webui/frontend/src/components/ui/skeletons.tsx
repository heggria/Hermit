interface CardGridSkeletonProps {
  /** Number of skeleton cards to show */
  readonly count?: number;
  /** Height class for each card (e.g., "h-28", "h-40") */
  readonly height?: string;
  /** Grid column classes */
  readonly columns?: string;
}

export function CardGridSkeleton({
  count = 6,
  height = 'h-28',
  columns = 'sm:grid-cols-2 lg:grid-cols-3',
}: CardGridSkeletonProps) {
  return (
    <div className={`grid gap-3 ${columns}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={`${height} animate-pulse rounded-2xl bg-muted`}
        />
      ))}
    </div>
  );
}

interface TextSkeletonProps {
  /** Number of lines */
  readonly lines?: number;
  /** Width class for lines */
  readonly width?: string;
}

export function TextSkeleton({ lines = 3, width = 'w-64' }: TextSkeletonProps) {
  return (
    <div className="space-y-3">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className={`h-5 ${width} animate-pulse rounded bg-muted`}
        />
      ))}
    </div>
  );
}

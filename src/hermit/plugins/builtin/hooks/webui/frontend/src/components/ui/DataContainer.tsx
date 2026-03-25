import type { ReactNode } from 'react';

interface DataContainerProps {
  /** Whether data is still loading */
  readonly isLoading: boolean;
  /** Whether the data set is empty (checked only when not loading) */
  readonly isEmpty: boolean;
  /** Skeleton / shimmer element shown while loading */
  readonly skeleton: ReactNode;
  /** Empty state element shown when data is empty */
  readonly emptyState: ReactNode;
  /** Actual content rendered when data is available */
  readonly children: ReactNode;
}

export function DataContainer({
  isLoading,
  isEmpty,
  skeleton,
  emptyState,
  children,
}: DataContainerProps) {
  if (isLoading) return <>{skeleton}</>;
  if (isEmpty) return <>{emptyState}</>;
  return <>{children}</>;
}

import { useMemo, useState } from 'react';

interface FilterTab {
  key: string;
  label: string;
  count: number;
}

interface UseFilteredDataResult<T> {
  /** Items matching the active filter */
  filtered: readonly T[];
  /** Currently selected filter key */
  activeTab: string;
  /** Set the active filter */
  setActiveTab: (tab: string) => void;
  /** Tab definitions with counts, ready for FilterTabs component */
  filterTabs: readonly FilterTab[];
}

/**
 * Generic hook for filtering a list by a status-like field
 * with automatic count computation and FilterTabs integration.
 *
 * The last entry in `filters` is treated as the "all" key (no filtering).
 */
export function useFilteredData<T>(
  items: readonly T[],
  filters: readonly string[],
  getStatus: (item: T) => string,
  labelFn: (key: string) => string,
  defaultFilter?: string,
): UseFilteredDataResult<T> {
  const allKey = filters[filters.length - 1];
  const [activeTab, setActiveTab] = useState(defaultFilter ?? filters[0]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      const status = getStatus(item);
      counts[status] = (counts[status] ?? 0) + 1;
    }
    return counts;
  }, [items, getStatus]);

  const filtered = useMemo(
    () =>
      activeTab === allKey
        ? items
        : items.filter((item) => getStatus(item) === activeTab),
    [items, activeTab, allKey, getStatus],
  );

  const filterTabs = useMemo(
    () =>
      filters.map((key) => ({
        key,
        label: labelFn(key),
        count: key === allKey ? items.length : (statusCounts[key] ?? 0),
      })),
    [filters, allKey, items.length, statusCounts, labelFn],
  );

  return { filtered, activeTab, setActiveTab, filterTabs };
}

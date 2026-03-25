// Project Memory tab -- reuses Memory page pattern scoped to program.

import { useState, useMemo, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Search, Brain } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { MemoryCard } from '@/components/memory/MemoryCard';
import { useProgramMemory } from '@/api/hooks';
import { DataContainer } from '@/components/ui/DataContainer';
import { EmptyState } from '@/components/layout/EmptyState';
import { CardGridSkeleton } from '@/components/ui/skeletons';
import { FilterTabs } from '@/components/ui/FilterTabs';
import { useFilteredData } from '@/hooks/useFilteredData';
import type { MemoryRecord } from '@/types';

const STATUS_FILTERS = ['active', 'invalidated', 'all'] as const;

export default function ProjectMemory() {
  const { t } = useTranslation();
  const { programId } = useParams<{ programId: string }>();
  const [search, setSearch] = useState('');

  const { data, isLoading } = useProgramMemory(programId ?? '');
  const memories = data?.memories ?? [];

  const getStatus = useCallback((m: MemoryRecord) => m.status, []);
  const labelFn = useCallback(
    (key: string) => {
      switch (key) {
        case 'all':
          return t('memory.filterAll');
        case 'active':
          return t('memory.filterActive');
        case 'invalidated':
          return t('memory.filterInvalidated');
        default:
          return key;
      }
    },
    [t],
  );

  const { filtered: statusFiltered, activeTab, setActiveTab, filterTabs } =
    useFilteredData(memories, STATUS_FILTERS, getStatus, labelFn);

  // Apply text search on top of status filter
  const filtered = useMemo(() => {
    if (!search.trim()) return statusFiltered;
    const query = search.toLowerCase();
    return statusFiltered.filter(
      (m) =>
        m.claim_text.toLowerCase().includes(query) ||
        m.category.toLowerCase().includes(query),
    );
  }, [statusFiltered, search]);

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-5 p-4 sm:p-6">
      {/* Search + filters row */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/50" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('memory.searchPlaceholder')}
            className="w-full rounded-lg border border-border bg-card/80 py-2 pl-10 pr-4 text-sm text-foreground placeholder:text-muted-foreground/50 outline-none transition-all focus:border-primary/50 focus:ring-2 focus:ring-primary/10 focus:bg-card"
          />
        </div>
        <FilterTabs
          tabs={filterTabs}
          activeTab={activeTab}
          onChange={setActiveTab}
        />
      </div>

      {/* Content */}
      <DataContainer
        isLoading={isLoading}
        isEmpty={filtered.length === 0}
        skeleton={
          <CardGridSkeleton
            count={6}
            height="h-40"
            columns="sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
          />
        }
        emptyState={
          <EmptyState
            icon={<Brain className="size-5 text-muted-foreground/60" />}
            title={t('memory.noResults')}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((memory) => (
            <MemoryCard key={memory.memory_id} memory={memory} />
          ))}
        </div>
      </DataContainer>
    </div>
  );
}

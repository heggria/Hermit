// Project Signals tab -- reuses Signals page pattern scoped to program.

import { useState, useMemo, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Radio, Search } from 'lucide-react';
import { SignalCard } from '@/components/signals/SignalCard';
import { useProgramSignals, useSignalAction } from '@/api/hooks';
import { DataContainer } from '@/components/ui/DataContainer';
import { EmptyState } from '@/components/layout/EmptyState';
import { CardGridSkeleton } from '@/components/ui/skeletons';
import { FilterTabs } from '@/components/ui/FilterTabs';
import { useFilteredData } from '@/hooks/useFilteredData';
import type { EvidenceSignal } from '@/types';

const DISPOSITION_FILTERS = ['pending', 'acted', 'dismissed', 'all'] as const;

export default function ProjectSignals() {
  const { t } = useTranslation();
  const { programId } = useParams<{ programId: string }>();
  const [search, setSearch] = useState('');

  const { data, isLoading } = useProgramSignals(programId ?? '');
  const signalAction = useSignalAction();

  const signals = data?.signals ?? [];

  const getStatus = useCallback((s: EvidenceSignal) => s.disposition, []);
  const labelFn = useCallback(
    (key: string) => {
      switch (key) {
        case 'all':
          return t('signals.filterAll');
        case 'pending':
          return t('signals.filterPending');
        case 'acted':
          return t('signals.filterActed');
        case 'dismissed':
          return t('signals.filterDismissed');
        default:
          return key;
      }
    },
    [t],
  );

  const { filtered: dispositionFiltered, activeTab, setActiveTab, filterTabs } =
    useFilteredData(signals, DISPOSITION_FILTERS, getStatus, labelFn);

  // Apply text search
  const filtered = useMemo(() => {
    if (!search.trim()) return dispositionFiltered;
    const query = search.toLowerCase();
    return dispositionFiltered.filter(
      (s) =>
        s.summary.toLowerCase().includes(query) ||
        s.source_kind.toLowerCase().includes(query) ||
        s.suggested_goal.toLowerCase().includes(query),
    );
  }, [dispositionFiltered, search]);

  const handleAct = (signalId: string) => {
    signalAction.mutate({ signalId, action: 'act' });
  };

  const handleSuppress = (signalId: string) => {
    signalAction.mutate({ signalId, action: 'suppress' });
  };

  const pendingCount = filterTabs.find((tab) => tab.key === 'pending')?.count ?? 0;

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-5 p-4 sm:p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-foreground">
          {t('signals.title')}
        </h2>
        {pendingCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-0.5 text-xs font-semibold text-primary-foreground">
            {t('signals.actionable', { count: pendingCount })}
          </span>
        )}
      </div>

      {/* Search + filters row */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/50" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('signals.loading').replace('...', '')}
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
            height="h-44"
            columns="sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
          />
        }
        emptyState={
          <EmptyState
            icon={<Radio className="size-5 text-muted-foreground/60" />}
            title={t('signals.noResults')}
            subtitle={t('signals.noResultsHint')}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((signal) => (
            <SignalCard
              key={signal.signal_id}
              signal={signal}
              onAct={handleAct}
              onSuppress={handleSuppress}
              isActing={signalAction.isPending}
            />
          ))}
        </div>
      </DataContainer>
    </div>
  );
}

// Project Signals tab -- reuses Signals page pattern scoped to program.

import { useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Radio } from 'lucide-react';
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

  const { filtered, activeTab, setActiveTab, filterTabs } = useFilteredData(
    signals,
    DISPOSITION_FILTERS,
    getStatus,
    labelFn,
  );

  const handleAct = (signalId: string) => {
    signalAction.mutate({ signalId, action: 'act' });
  };

  const handleSuppress = (signalId: string) => {
    signalAction.mutate({ signalId, action: 'suppress' });
  };

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6 p-4 sm:p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-foreground">
          {t('signals.title')}
        </h2>
        {filterTabs.find((tab) => tab.key === 'pending')?.count
          ? (
            <span className="inline-flex items-center rounded-full bg-primary px-3 py-0.5 text-xs font-semibold text-primary-foreground">
              {t('signals.actionable', {
                count: filterTabs.find((tab) => tab.key === 'pending')!.count,
              })}
            </span>
          )
          : null}
      </div>

      {/* Filter pills */}
      <FilterTabs
        tabs={filterTabs}
        activeTab={activeTab}
        onChange={setActiveTab}
      />

      {/* Content */}
      <DataContainer
        isLoading={isLoading}
        isEmpty={filtered.length === 0}
        skeleton={
          <CardGridSkeleton
            count={6}
            height="h-32"
            columns="sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
          />
        }
        emptyState={
          <EmptyState
            icon={<Radio className="size-5 text-muted-foreground/60" />}
            title={t('signals.noResults')}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
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

// Teams list page — displays teams with status filter.

import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Users } from 'lucide-react';
import { useTeamList } from '@/api/hooks';
import { TeamCard } from '@/components/teams/TeamCard';
import { CreateTeamDialog } from '@/components/teams/CreateTeamDialog';
import { PageHeader } from '@/components/layout/PageHeader';
import { EmptyState } from '@/components/layout/EmptyState';
import { CardGridSkeleton } from '@/components/ui/skeletons';
import { DataContainer } from '@/components/ui/DataContainer';
import { FilterTabs } from '@/components/ui/FilterTabs';
import { useFilteredData } from '@/hooks/useFilteredData';

const STATUS_FILTERS = ['active', 'paused', 'blocked', 'archived', 'all'] as const;

export default function Teams() {
  const { t } = useTranslation();
  const [dialogOpen, setDialogOpen] = useState(false);

  const { data: teamsData, isLoading } = useTeamList();
  const allTeams = teamsData?.teams ?? [];

  const getStatus = useCallback((team: (typeof allTeams)[number]) => team.status, []);
  const labelFn = useCallback(
    (key: string) =>
      key === 'all' ? t('teams.allStatuses') : t(`common.status.${key}`, key),
    [t],
  );

  const { filtered: teams, activeTab: statusFilter, setActiveTab: setStatusFilter, filterTabs } =
    useFilteredData(allTeams, STATUS_FILTERS, getStatus, labelFn);

  return (
    <div className="space-y-4">
      <PageHeader
        title={t('teams.title')}
        subtitle={t('teams.subtitle')}
        action={{
          label: t('teams.create'),
          icon: <Plus className="size-4" />,
          onClick: () => setDialogOpen(true),
        }}
      />

      <div className="flex items-center justify-between gap-3">
        <FilterTabs
          tabs={filterTabs}
          activeTab={statusFilter}
          onChange={setStatusFilter}
          size="sm"
        />
      </div>

      <DataContainer
        isLoading={isLoading}
        isEmpty={teams.length === 0}
        skeleton={<CardGridSkeleton count={3} height="h-32" />}
        emptyState={
          <EmptyState
            icon={<Users className="size-5 text-muted-foreground/60" />}
            title={
              statusFilter === 'all'
                ? t('teams.emptyState')
                : t('teams.emptyStatusState', {
                    status: t(`common.status.${statusFilter}`, statusFilter),
                  })
            }
            subtitle={statusFilter === 'all' ? t('teams.emptyStateHint') : undefined}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => (
            <TeamCard
              key={team.team_id}
              team={team}
            />
          ))}
        </div>
      </DataContainer>

      <CreateTeamDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

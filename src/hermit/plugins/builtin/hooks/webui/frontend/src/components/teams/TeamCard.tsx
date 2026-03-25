// Team card component for the teams list view.

import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Users } from 'lucide-react';
import { cn } from '@/lib/utils';
import { getStatusStyle } from '@/lib/status-styles';
import type { TeamRecord } from '@/types';

const ROLE_COLORS = [
  'bg-primary/10 text-primary dark:bg-primary/20',
  'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300',
  'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
  'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
];

interface TeamCardProps {
  readonly team: TeamRecord;
  readonly programName?: string;
}

export function TeamCard({ team, programName }: TeamCardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const roleSlots = Object.values(team.role_assembly ?? {});
  const totalWorkers = roleSlots.reduce((sum, s) => sum + s.count, 0);
  const statusCfg = getStatusStyle(team.status);

  return (
    <div
      className="group cursor-pointer rounded-2xl border border-border bg-card p-4 transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 space-y-3"
      onClick={() => navigate(`/teams/${team.team_id}`)}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10">
            <Users className="size-4 text-primary" />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-foreground">
              {team.title}
            </h3>
            {programName && (
              <p className="truncate text-xs text-muted-foreground mt-0.5">
                {programName}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium',
              statusCfg.bg,
              statusCfg.text,
            )}
          >
            <span className={cn('size-1.5 rounded-full', statusCfg.dot)} />
            {t(`common.status.${team.status}`, team.status)}
          </span>
          <ChevronRight className="size-4 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5" />
        </div>
      </div>

      {/* Roles as chips */}
      {roleSlots.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {roleSlots.map((slot, i) => (
            <span
              key={slot.role}
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium',
                ROLE_COLORS[i % ROLE_COLORS.length],
              )}
            >
              {slot.role}
              {slot.count > 1 && (
                <span className="opacity-60">&times;{slot.count}</span>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Footer meta */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        {totalWorkers > 0 && (
          <span className="flex items-center gap-1">
            <Users className="size-3" />
            {totalWorkers}
          </span>
        )}
      </div>
    </div>
  );
}

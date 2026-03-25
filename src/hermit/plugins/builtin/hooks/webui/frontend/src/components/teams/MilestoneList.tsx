// Milestone list panel for the team detail sheet.

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Milestone as MilestoneIcon,
  Plus,
  ChevronRight,
  X,
  Flag,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { getStatusStyle } from '@/lib/status-styles';
import { useCreateMilestone, useUpdateMilestone } from '@/api/hooks';
import type { MilestoneRecord } from '@/types';

const STATUS_TRANSITIONS: Record<string, string[]> = {
  pending: ['active', 'skipped'],
  active: ['completed', 'blocked', 'failed'],
  blocked: ['active', 'failed', 'skipped'],
};

interface MilestoneListProps {
  readonly teamId: string;
  readonly milestones: readonly MilestoneRecord[];
  readonly onClose?: () => void;
}

export function MilestoneList({ teamId, milestones, onClose }: MilestoneListProps) {
  const { t } = useTranslation();
  const createMilestone = useCreateMilestone();
  const updateMilestone = useUpdateMilestone();

  const [newTitle, setNewTitle] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [showForm, setShowForm] = useState(false);

  const completedCount = milestones.filter((m) => m.status === 'completed').length;
  const progressPercent = milestones.length > 0 ? (completedCount / milestones.length) * 100 : 0;

  const handleAdd = useCallback(() => {
    if (!newTitle.trim()) return;
    createMilestone.mutate(
      {
        team_id: teamId,
        title: newTitle.trim(),
        description: newDescription.trim() || undefined,
      },
      {
        onSuccess: () => {
          setNewTitle('');
          setNewDescription('');
          setShowForm(false);
        },
      },
    );
  }, [teamId, newTitle, newDescription, createMilestone]);

  const handleStatusChange = useCallback(
    (milestoneId: string, newStatus: string) => {
      updateMilestone.mutate({
        teamId,
        milestoneId,
        status: newStatus,
      });
    },
    [teamId, updateMilestone],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <MilestoneIcon className="size-4 text-primary" />
          <h3 className="text-sm font-semibold text-foreground">
            {t('teams.milestones')}
          </h3>
          {milestones.length > 0 && (
            <span className="text-[10px] tabular-nums text-muted-foreground bg-secondary rounded-full px-1.5 py-0.5">
              {completedCount}/{milestones.length}
            </span>
          )}
        </div>
        {onClose && (
          <Button variant="ghost" size="icon-xs" onClick={onClose}>
            <X className="size-3.5" />
          </Button>
        )}
      </div>

      {/* Progress bar */}
      {milestones.length > 0 && (
        <div className="px-4 pt-3 pb-1 shrink-0">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
              {t('teams.milestone.progress', {
                completed: completedCount,
                total: milestones.length,
              })}
            </span>
            <span className="text-[10px] tabular-nums font-medium text-muted-foreground">
              {Math.round(progressPercent)}%
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-secondary overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-500 ease-out',
                progressPercent === 100 ? 'bg-green-500' : 'bg-primary',
              )}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      )}

      {/* Milestone items */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {milestones.length === 0 && !showForm && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="rounded-full bg-secondary/80 p-3 mb-3">
              <Flag className="size-5 text-muted-foreground/60" />
            </div>
            <p className="text-sm font-medium text-muted-foreground mb-1">
              {t('teams.milestone.empty')}
            </p>
            <p className="text-xs text-muted-foreground/60 max-w-[200px]">
              {t('teams.milestone.emptyHint')}
            </p>
          </div>
        )}

        {milestones.map((m, index) => {
          const transitions = STATUS_TRANSITIONS[m.status] ?? [];
          const milestoneStatusStyle = getStatusStyle(m.status);
          const isCompleted = m.status === 'completed';
          const isTerminal = m.status === 'completed' || m.status === 'skipped' || m.status === 'failed';

          return (
            <div
              key={m.milestone_id}
              className={cn(
                'group rounded-lg border p-3 transition-colors',
                isCompleted
                  ? 'border-green-200 bg-green-50/50 dark:border-green-900/30 dark:bg-green-950/20'
                  : 'border-border bg-card hover:bg-secondary/30',
              )}
            >
              <div className="flex items-start gap-3">
                {/* Step indicator */}
                <div
                  className={cn(
                    'flex items-center justify-center size-6 rounded-full shrink-0 mt-0.5 text-[10px] font-bold',
                    isCompleted
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400'
                      : isTerminal
                        ? 'bg-secondary text-muted-foreground'
                        : 'bg-primary/10 text-primary',
                  )}
                >
                  {isCompleted ? '\u2713' : index + 1}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <p
                      className={cn(
                        'text-sm font-medium leading-snug',
                        isCompleted
                          ? 'text-green-700 dark:text-green-400 line-through decoration-green-400/40'
                          : 'text-foreground',
                      )}
                    >
                      {m.title}
                    </p>
                    <span
                      className={cn(
                        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0',
                        milestoneStatusStyle.bg,
                        milestoneStatusStyle.text,
                      )}
                    >
                      {t(`teams.milestone.${m.status}`, m.status)}
                    </span>
                  </div>

                  {m.description && (
                    <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
                      {m.description}
                    </p>
                  )}

                  {transitions.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {transitions.map((nextStatus) => (
                        <Button
                          key={nextStatus}
                          variant="outline"
                          size="xs"
                          className="h-6 text-[10px] gap-0.5"
                          onClick={() => handleStatusChange(m.milestone_id, nextStatus)}
                          disabled={updateMilestone.isPending}
                        >
                          <ChevronRight className="size-2.5" />
                          {t(`teams.milestone.${nextStatus}`, nextStatus)}
                        </Button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}

        {/* Add milestone form */}
        {showForm && (
          <div className="rounded-lg border border-dashed border-primary/30 bg-primary/5 p-3 space-y-2">
            <Input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder={t('teams.milestone.titlePlaceholder')}
              className="text-sm"
              autoFocus
            />
            <Textarea
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              placeholder={t('teams.milestone.descriptionPlaceholder')}
              className="min-h-[60px] text-sm resize-none"
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleAdd}
                disabled={!newTitle.trim() || createMilestone.isPending}
              >
                {createMilestone.isPending
                  ? t('teams.milestone.adding')
                  : t('teams.milestone.add')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setShowForm(false);
                  setNewTitle('');
                  setNewDescription('');
                }}
              >
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Add button */}
      {!showForm && (
        <div className="px-4 py-3 border-t border-border shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="w-full gap-1.5"
            onClick={() => setShowForm(true)}
          >
            <Plus className="size-3.5" />
            {t('teams.milestone.addTitle')}
          </Button>
        </div>
      )}
    </div>
  );
}

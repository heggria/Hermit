// Milestone list sidebar panel for the team detail page.

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Milestone as MilestoneIcon, Plus, ChevronRight } from 'lucide-react';
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
}

export function MilestoneList({ teamId, milestones }: MilestoneListProps) {
  const { t } = useTranslation();
  const createMilestone = useCreateMilestone();
  const updateMilestone = useUpdateMilestone();

  const [newTitle, setNewTitle] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [showForm, setShowForm] = useState(false);

  const completedCount = milestones.filter((m) => m.status === 'completed').length;

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
      <div className="flex items-center justify-between px-4 h-9 border-b border-border">
        <div className="flex items-center gap-2">
          <MilestoneIcon className="size-4 text-primary" />
          <h3 className="text-sm font-semibold text-foreground">
            {t('teams.milestones')}
          </h3>
        </div>
        {milestones.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {t('teams.milestone.progress', {
              completed: completedCount,
              total: milestones.length,
            })}
          </span>
        )}
      </div>

      {/* Progress bar */}
      {milestones.length > 0 && (
        <div className="px-4 pt-3">
          <div className="h-1.5 w-full rounded-full bg-secondary">
            <div
              className="h-1.5 rounded-full bg-primary transition-all"
              style={{
                width: `${(completedCount / milestones.length) * 100}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Milestone items */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        {milestones.length === 0 && !showForm && (
          <p className="text-xs text-muted-foreground py-4 text-center">
            {t('teams.milestone.empty')}
          </p>
        )}

        {milestones.map((m) => {
          const transitions = STATUS_TRANSITIONS[m.status] ?? [];
          const milestoneStatusStyle = getStatusStyle(m.status);
          return (
            <div
              key={m.milestone_id}
              className="rounded-lg border border-border bg-secondary/30 p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-foreground leading-snug">
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
                <div className="mt-2 flex gap-1">
                  {transitions.map((nextStatus) => (
                    <Button
                      key={nextStatus}
                      variant="outline"
                      size="xs"
                      onClick={() => handleStatusChange(m.milestone_id, nextStatus)}
                      disabled={updateMilestone.isPending}
                    >
                      <ChevronRight className="size-3" />
                      {t(`teams.milestone.${nextStatus}`, nextStatus)}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {/* Add milestone form */}
        {showForm && (
          <div className="rounded-lg border border-dashed border-border p-3 space-y-2">
            <Input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder={t('teams.milestone.titlePlaceholder')}
              className="text-sm"
            />
            <Textarea
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              placeholder={t('teams.milestone.descriptionPlaceholder')}
              className="min-h-[60px] text-sm"
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
                variant="outline"
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
        <div className="px-4 py-3 border-t border-border">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
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

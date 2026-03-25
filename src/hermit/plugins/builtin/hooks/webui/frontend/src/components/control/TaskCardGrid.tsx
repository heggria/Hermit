// Unified task card list with all active/blocked/recent tasks in a single view.

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Inbox } from 'lucide-react';
import { TaskCard } from '@/components/control/TaskCard';
import type { TaskRecord } from '@/types';

const TWENTY_FOUR_HOURS = 24 * 60 * 60;

interface TaskCardGridProps {
  readonly tasks: readonly TaskRecord[];
  readonly archivedIds: Set<string>;
  readonly onArchive: (id: string) => void;
  readonly selectedId: string | null;
  readonly onSelect: (id: string) => void;
}

function statusPriority(status: string): number {
  switch (status) {
    case 'blocked':
      return 0;
    case 'running':
      return 1;
    case 'queued':
      return 2;
    case 'reconciling':
      return 3;
    case 'completed':
      return 4;
    case 'failed':
      return 5;
    case 'cancelled':
      return 6;
    default:
      return 7;
  }
}

function isVisible(task: TaskRecord): boolean {
  // Active statuses
  if (task.status === 'running' || task.status === 'queued' || task.status === 'reconciling') {
    return true;
  }
  // Blocked
  if (task.status === 'blocked') return true;
  // Recently completed (within 24h)
  if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
    const nowSec = Date.now() / 1000;
    return nowSec - task.updated_at < TWENTY_FOUR_HOURS;
  }
  return false;
}

export function TaskCardGrid({
  tasks,
  archivedIds,
  onArchive,
  selectedId,
  onSelect,
}: TaskCardGridProps) {
  const { t } = useTranslation();

  const visibleTasks = useMemo(() => {
    const filtered = tasks.filter(
      (task) => !archivedIds.has(task.task_id) && isVisible(task),
    );
    return [...filtered].sort((a, b) => {
      const pa = statusPriority(a.status);
      const pb = statusPriority(b.status);
      if (pa !== pb) return pa - pb;
      return b.updated_at - a.updated_at;
    });
  }, [tasks, archivedIds]);

  if (visibleTasks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <Inbox className="mb-3 size-10 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">{t('controlCenter.emptyActive')}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {visibleTasks.map((task) => (
        <div key={task.task_id} className="animate-slide-up">
          <TaskCard
            task={task}
            selected={task.task_id === selectedId}
            onSelect={() => onSelect(task.task_id)}
            onArchive={() => onArchive(task.task_id)}
          />
        </div>
      ))}
    </div>
  );
}

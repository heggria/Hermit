// Filterable task card grid with Active/Blocked/Recent/Archived tabs.

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Archive, Inbox } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { TaskCard } from '@/components/control/TaskCard';
import type { TaskRecord } from '@/types';

type TabValue = 'active' | 'blocked' | 'recent' | 'archived';

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

function isActiveStatus(status: string): boolean {
  return status === 'running' || status === 'queued' || status === 'reconciling';
}

function isRecentCompleted(task: TaskRecord): boolean {
  if (task.status !== 'completed' && task.status !== 'failed' && task.status !== 'cancelled') {
    return false;
  }
  const nowSec = Date.now() / 1000;
  return nowSec - task.updated_at < TWENTY_FOUR_HOURS;
}

function sortTasks(tasks: readonly TaskRecord[]): readonly TaskRecord[] {
  return [...tasks].sort((a, b) => {
    const pa = statusPriority(a.status);
    const pb = statusPriority(b.status);
    if (pa !== pb) return pa - pb;
    return b.updated_at - a.updated_at;
  });
}

interface EmptyTabProps {
  readonly message: string;
}

function EmptyTab({ message }: EmptyTabProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Inbox className="mb-3 size-10 text-muted-foreground/40" />
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

interface CardGridProps {
  readonly tasks: readonly TaskRecord[];
  readonly selectedId: string | null;
  readonly onSelect: (id: string) => void;
  readonly onArchive: (id: string) => void;
}

function CardGrid({ tasks, selectedId, onSelect, onArchive }: CardGridProps) {
  return (
    <div className="space-y-2">
      {tasks.map((task) => (
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

export function TaskCardGrid({
  tasks,
  archivedIds,
  onArchive,
  selectedId,
  onSelect,
}: TaskCardGridProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabValue>('active');

  const { activeTasks, blockedTasks, recentTasks, archivedTasks } = useMemo(() => {
    const active: TaskRecord[] = [];
    const blocked: TaskRecord[] = [];
    const recent: TaskRecord[] = [];
    const archived: TaskRecord[] = [];

    for (const task of tasks) {
      if (archivedIds.has(task.task_id)) {
        archived.push(task);
        continue;
      }
      if (task.status === 'blocked') {
        blocked.push(task);
      } else if (isActiveStatus(task.status)) {
        active.push(task);
      } else if (isRecentCompleted(task)) {
        recent.push(task);
      }
    }

    return {
      activeTasks: sortTasks(active) as TaskRecord[],
      blockedTasks: sortTasks(blocked) as TaskRecord[],
      recentTasks: sortTasks(recent) as TaskRecord[],
      archivedTasks: sortTasks(archived) as TaskRecord[],
    };
  }, [tasks, archivedIds]);

  const handleArchiveAllCompleted = () => {
    for (const task of recentTasks) {
      onArchive(task.task_id);
    }
  };

  return (
    <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as TabValue)}>
      <div className="flex items-center justify-between">
        <TabsList>
          <TabsTrigger value="active" className="gap-1.5">
            {t('controlCenter.tabs.active')}
            {activeTasks.length > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 min-w-4 px-1 text-[10px]">
                {activeTasks.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="blocked" className="gap-1.5">
            {t('controlCenter.tabs.blocked')}
            {blockedTasks.length > 0 && (
              <Badge
                className={cn(
                  'ml-1 h-4 min-w-4 px-1 text-[10px]',
                  'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400',
                )}
              >
                {blockedTasks.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="recent" className="gap-1.5">
            {t('controlCenter.tabs.recent')}
            {recentTasks.length > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 min-w-4 px-1 text-[10px]">
                {recentTasks.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="archived" className="gap-1.5">
            {t('controlCenter.tabs.archived')}
            {archivedTasks.length > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 min-w-4 px-1 text-[10px]">
                {archivedTasks.length}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {activeTab === 'recent' && recentTasks.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleArchiveAllCompleted}
            className="gap-1.5 text-xs text-muted-foreground"
          >
            <Archive className="size-3" />
            {t('controlCenter.archiveAll')}
          </Button>
        )}
      </div>

      <TabsContent value="active">
        {activeTasks.length === 0 ? (
          <EmptyTab message={t('controlCenter.emptyActive')} />
        ) : (
          <CardGrid
            tasks={activeTasks}
            selectedId={selectedId}
            onSelect={onSelect}
            onArchive={onArchive}
          />
        )}
      </TabsContent>

      <TabsContent value="blocked">
        {blockedTasks.length === 0 ? (
          <EmptyTab message={t('controlCenter.emptyBlocked')} />
        ) : (
          <CardGrid
            tasks={blockedTasks}
            selectedId={selectedId}
            onSelect={onSelect}
            onArchive={onArchive}
          />
        )}
      </TabsContent>

      <TabsContent value="recent">
        {recentTasks.length === 0 ? (
          <EmptyTab message={t('controlCenter.emptyRecent')} />
        ) : (
          <CardGrid
            tasks={recentTasks}
            selectedId={selectedId}
            onSelect={onSelect}
            onArchive={onArchive}
          />
        )}
      </TabsContent>

      <TabsContent value="archived">
        {archivedTasks.length === 0 ? (
          <EmptyTab message={t('controlCenter.emptyArchived')} />
        ) : (
          <CardGrid
            tasks={archivedTasks}
            selectedId={selectedId}
            onSelect={onSelect}
            onArchive={onArchive}
          />
        )}
      </TabsContent>
    </Tabs>
  );
}

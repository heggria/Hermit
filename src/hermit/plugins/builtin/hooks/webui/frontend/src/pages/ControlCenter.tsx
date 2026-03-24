// Control Center page -- split-pane layout: left = task list, right = task detail.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Terminal } from 'lucide-react';
import { useTaskList } from '@/api/hooks';
import { useTaskStream } from '@/hooks/useTaskStream';
import { TaskInputBar } from '@/components/control/TaskInputBar';
import { TaskCardGrid } from '@/components/control/TaskCardGrid';
import { TaskDetailPanel } from '@/components/control/TaskDetailPanel';

const ARCHIVED_STORAGE_KEY = 'hermit-archived-tasks';
const AUTO_ARCHIVE_DELAY_MS = 5000;

function loadArchivedIds(): Set<string> {
  try {
    const saved = localStorage.getItem(ARCHIVED_STORAGE_KEY);
    return saved ? new Set(JSON.parse(saved) as string[]) : new Set();
  } catch {
    return new Set();
  }
}

function EmptyDetailPanel() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <Terminal className="mx-auto mb-3 size-12 text-muted-foreground/20" />
        <p className="text-sm text-muted-foreground">
          {t('controlCenter.selectTask')}
        </p>
        <p className="mt-1 text-xs text-muted-foreground/60">
          {t('controlCenter.selectTaskHint')}
        </p>
      </div>
    </div>
  );
}

export default function ControlCenter() {
  const { t } = useTranslation();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [archivedIds, setArchivedIds] = useState<Set<string>>(loadArchivedIds);
  const [flashBlockedId, setFlashBlockedId] = useState<string | null>(null);
  const autoArchiveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevBlockedIdsRef = useRef<Set<string>>(new Set());

  // SSE for live updates
  useTaskStream();

  // Fetch all tasks
  const { data } = useTaskList(undefined, 100);
  const tasks = data?.tasks ?? [];

  const selectedTask = tasks.find((t) => t.task_id === selectedTaskId) ?? null;

  // Persist archived IDs to localStorage
  useEffect(() => {
    localStorage.setItem(ARCHIVED_STORAGE_KEY, JSON.stringify([...archivedIds]));
  }, [archivedIds]);

  // Detect newly blocked tasks and flash highlight
  useEffect(() => {
    const currentBlockedIds = new Set(
      tasks.filter((task) => task.status === 'blocked').map((task) => task.task_id),
    );
    for (const id of currentBlockedIds) {
      if (!prevBlockedIdsRef.current.has(id)) {
        setFlashBlockedId(id);
        setTimeout(() => setFlashBlockedId(null), 2000);
        break;
      }
    }
    prevBlockedIdsRef.current = currentBlockedIds;
  }, [tasks]);

  // Auto-archive: when a completed task is selected (viewed), start a 5s timer
  useEffect(() => {
    if (autoArchiveTimerRef.current) {
      clearTimeout(autoArchiveTimerRef.current);
      autoArchiveTimerRef.current = null;
    }

    if (!selectedTaskId) return;

    const selected = tasks.find((task) => task.task_id === selectedTaskId);
    if (!selected) return;

    const isCompleted =
      selected.status === 'completed' ||
      selected.status === 'failed' ||
      selected.status === 'cancelled';

    if (isCompleted && !archivedIds.has(selectedTaskId)) {
      autoArchiveTimerRef.current = setTimeout(() => {
        setArchivedIds((prev) => new Set([...prev, selectedTaskId]));
        autoArchiveTimerRef.current = null;
      }, AUTO_ARCHIVE_DELAY_MS);
    }

    return () => {
      if (autoArchiveTimerRef.current) {
        clearTimeout(autoArchiveTimerRef.current);
        autoArchiveTimerRef.current = null;
      }
    };
  }, [selectedTaskId, tasks, archivedIds]);

  const handleArchive = useCallback((id: string) => {
    setArchivedIds((prev) => new Set([...prev, id]));
  }, []);

  const handleSelect = useCallback((id: string) => {
    setSelectedTaskId((prev) => (prev === id ? null : id));
  }, []);

  return (
    <div className="flex h-full gap-0">
      {/* Left Panel: Task List */}
      <div className="flex w-1/2 flex-col border-r border-border/50 overflow-hidden">
        {/* Header + Input */}
        <div className="shrink-0 space-y-3 p-4 pb-2">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              {t('controlCenter.title')}
            </h1>
            <p className="text-xs text-muted-foreground">
              {t('controlCenter.subtitle')}
            </p>
          </div>
          <TaskInputBar />
        </div>

        {/* Blocked task flash notification */}
        {flashBlockedId && (
          <div className="mx-4 mb-2 animate-slide-up rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-800/50 dark:bg-amber-900/20 dark:text-amber-300">
            {t('controlCenter.blockedNotice')}
          </div>
        )}

        {/* Task Cards - scrollable */}
        <div className="flex-1 overflow-y-auto p-4 pt-2">
          <TaskCardGrid
            tasks={tasks}
            archivedIds={archivedIds}
            onArchive={handleArchive}
            selectedId={selectedTaskId}
            onSelect={handleSelect}
          />
        </div>
      </div>

      {/* Right Panel: Task Detail */}
      <div className="flex w-1/2 flex-col overflow-hidden">
        {selectedTask ? (
          <TaskDetailPanel
            task={selectedTask}
            onClose={() => setSelectedTaskId(null)}
          />
        ) : (
          <EmptyDetailPanel />
        )}
      </div>
    </div>
  );
}

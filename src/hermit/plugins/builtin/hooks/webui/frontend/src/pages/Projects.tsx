// Projects split layout — project list on left, detail panel on right.

import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, Outlet, useNavigate } from 'react-router-dom';
import { Plus, FolderOpen } from 'lucide-react';
import { useProgramList } from '@/api/hooks';
import { ProgramListItem } from '@/components/projects/ProgramCard';
import { CreateProgramDialog } from '@/components/projects/CreateProgramDialog';
import { FilterTabs } from '@/components/ui/FilterTabs';

type StatusFilter = 'active' | 'archived';

const STATUS_FILTERS: StatusFilter[] = ['active', 'archived'];

const LAST_SEEN_KEY = 'hermit-project-last-seen';

// ---------------------------------------------------------------------------
// Unread tracking — stores { [programId]: lastSeenUpdatedAt } in localStorage
// ---------------------------------------------------------------------------

function loadLastSeen(): Record<string, number> {
  try {
    const raw = localStorage.getItem(LAST_SEEN_KEY);
    return raw ? (JSON.parse(raw) as Record<string, number>) : {};
  } catch {
    return {};
  }
}

function saveLastSeen(map: Record<string, number>) {
  localStorage.setItem(LAST_SEEN_KEY, JSON.stringify(map));
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function EmptyDetailPlaceholder() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <FolderOpen className="mx-auto mb-3 size-12 text-muted-foreground/15" />
        <p className="text-sm text-muted-foreground">
          {t('projects.selectProject')}
        </p>
      </div>
    </div>
  );
}

export default function Projects() {
  const { t } = useTranslation();
  const { programId } = useParams<{ programId: string }>();
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [lastSeen, setLastSeen] = useState<Record<string, number>>(loadLastSeen);

  // Auto-restore last visited project when landing on root with no selection
  useEffect(() => {
    if (programId) return;
    const saved = localStorage.getItem('hermit-last-project');
    if (saved) {
      navigate(`/projects/${saved}/tasks`, { replace: true });
    }
  }, [programId, navigate]);

  // Mark current project as "read" when selected
  useEffect(() => {
    if (!programId) return;
    setLastSeen((prev) => {
      const next = { ...prev, [programId]: Date.now() / 1000 };
      saveLastSeen(next);
      return next;
    });
  }, [programId]);

  const { data, isLoading } = useProgramList(statusFilter, 100);
  const programs = data?.programs ?? [];

  // Determine which projects have unread updates
  const unreadSet = useMemo(() => {
    const set = new Set<string>();
    for (const p of programs) {
      const seen = lastSeen[p.program_id];
      if (!seen || p.updated_at > seen) {
        set.add(p.program_id);
      }
    }
    return set;
  }, [programs, lastSeen]);

  // Sort: unread projects first, then by updated_at descending
  const sortedPrograms = useMemo(() => {
    return [...programs].sort((a, b) => {
      const aUnread = unreadSet.has(a.program_id) ? 1 : 0;
      const bUnread = unreadSet.has(b.program_id) ? 1 : 0;
      if (aUnread !== bUnread) return bUnread - aUnread;
      return b.updated_at - a.updated_at;
    });
  }, [programs, unreadSet]);

  const filterTabs = STATUS_FILTERS.map((status) => ({
    key: status,
    label: t(`common.status.${status}`, status),
  }));

  return (
    <div className="flex h-full">
      {/* Left: project list */}
      <div className="flex w-64 shrink-0 flex-col border-r border-border/50">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between px-4 pt-4 pb-2">
          <h2 className="text-sm font-semibold text-foreground">
            {t('projects.title')}
          </h2>
          <button
            type="button"
            onClick={() => setDialogOpen(true)}
            className="flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label={t('projects.create')}
            data-tour-id="create-project-btn"
          >
            <Plus className="size-4" />
          </button>
        </div>

        {/* Filter tabs */}
        <div className="shrink-0 px-4 pb-2">
          <FilterTabs
            tabs={filterTabs}
            activeTab={statusFilter}
            onChange={setStatusFilter}
            showCount={false}
            size="sm"
          />
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {isLoading && (
            <div className="space-y-1 px-1">
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="h-9 animate-pulse rounded-lg bg-muted/50"
                />
              ))}
            </div>
          )}

          {!isLoading && programs.length === 0 && (
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <FolderOpen className="mb-2 size-6 text-muted-foreground/30" />
              <p className="text-xs text-muted-foreground">
                {t('projects.emptyState')}
              </p>
            </div>
          )}

          {!isLoading && sortedPrograms.length > 0 && (
            <div className="space-y-0.5">
              {sortedPrograms.map((program) => (
                <ProgramListItem
                  key={program.program_id}
                  program={program}
                  hasUnread={unreadSet.has(program.program_id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right: project detail (from nested route Outlet) */}
      <div className="flex-1 overflow-hidden">
        {programId ? <Outlet /> : <EmptyDetailPlaceholder />}
      </div>

      <CreateProgramDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

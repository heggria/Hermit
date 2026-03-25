// Project detail — right panel of the split layout with header and tabbed navigation.

import { useEffect, useState } from 'react';
import { useParams, NavLink, Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  ListTodo,
  Brain,
  Zap,
  ShieldCheck,
  Scale,
  MessageSquare,
  Archive,
  RotateCcw,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { getStatusStyle } from '@/lib/status-styles';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useProgram, useUpdateProgramStatus } from '@/api/hooks';

// Tab configuration
const TABS = [
  { path: 'tasks', icon: ListTodo },
  { path: 'memory', icon: Brain },
  { path: 'signals', icon: Zap },
  { path: 'approvals', icon: ShieldCheck },
  { path: 'policy', icon: Scale },
  { path: 'chat', icon: MessageSquare },
] as const;

export default function ProjectDetail() {
  const { t } = useTranslation();
  const { programId } = useParams<{ programId: string }>();

  // Persist last visited project for auto-restore on next visit
  useEffect(() => {
    if (programId) {
      localStorage.setItem('hermit-last-project', programId);
    }
  }, [programId]);

  const { data, isLoading } = useProgram(programId ?? '');
  const updateStatus = useUpdateProgramStatus();

  const program = data?.program ?? null;

  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmInput, setConfirmInput] = useState('');

  const isArchived = program?.status === 'archived';
  const confirmMatches = confirmInput.trim() === (program?.title ?? '').trim();

  const handleArchive = () => {
    if (!programId || !confirmMatches) return;
    updateStatus.mutate(
      { programId, status: 'archived' },
      { onSuccess: () => { setDialogOpen(false); setConfirmInput(''); } },
    );
  };

  const handleReactivate = () => {
    if (!programId) return;
    updateStatus.mutate({ programId, status: 'active' });
  };

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex items-center gap-3">
          <div className="size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
        </div>
      </div>
    );
  }

  if (!program) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-muted-foreground">{t('common.noData')}</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="shrink-0 space-y-2 border-b border-border/50 px-4 py-3">
        {/* Title + status + action */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <h1 className="truncate text-[15px] font-semibold tracking-tight text-foreground">
              {program.title}
              <span className="ml-2 align-middle text-[10px] font-mono font-normal text-muted-foreground/50">{programId}</span>
            </h1>
            <Badge
              variant="secondary"
              className={cn(
                'shrink-0 text-[10px] px-1.5 py-0',
                getStatusStyle(program.status).bg,
                getStatusStyle(program.status).text,
              )}
            >
              {t(`common.status.${program.status}`, program.status)}
            </Badge>
          </div>

          {/* Archive / Reactivate button */}
          {isArchived ? (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 text-xs h-7"
              onClick={handleReactivate}
              disabled={updateStatus.isPending}
            >
              <RotateCcw className="size-3" />
              {t('projects.reactivate')}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 text-xs h-7 text-muted-foreground hover:text-destructive hover:border-destructive/50"
              onClick={() => setDialogOpen(true)}
            >
              <Archive className="size-3" />
              {t('projects.archive')}
            </Button>
          )}
        </div>

        {/* Tab bar */}
        <nav className="flex gap-0.5" data-tour-id="project-tabs">
          {TABS.map(({ path, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground shadow-sm'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                )
              }
            >
              <Icon className="size-3" />
              {t(`projects.tabs.${path}`)}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        <Outlet />
      </div>

      {/* Archive confirmation dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => { setDialogOpen(open); if (!open) setConfirmInput(''); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('projects.archiveDialog.title')}</DialogTitle>
            <DialogDescription>
              {t('projects.archiveDialog.description', { name: program.title })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <p className="text-sm text-muted-foreground">
              {t('projects.archiveDialog.prompt', { name: program.title })}
            </p>
            <Input
              value={confirmInput}
              onChange={(e) => setConfirmInput(e.target.value)}
              placeholder={program.title}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setDialogOpen(false); setConfirmInput(''); }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={!confirmMatches || updateStatus.isPending}
              onClick={handleArchive}
            >
              <Archive className="size-3.5" />
              {t('projects.archive')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

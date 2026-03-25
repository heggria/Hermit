// Flat list item for the project sidebar.

import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { FolderOpen, MoreHorizontal, Archive, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useUpdateProgramStatus } from '@/api/hooks';
import type { ProgramRecord } from '@/types';

interface ProgramListItemProps {
  readonly program: ProgramRecord;
  readonly hasUnread?: boolean;
}

export function ProgramListItem({ program, hasUnread }: ProgramListItemProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { programId: activeProgramId } = useParams<{ programId: string }>();
  const updateStatus = useUpdateProgramStatus();

  const isSelected = activeProgramId === program.program_id;
  const isArchived = program.status === 'archived';

  return (
    <div
      className={cn(
        'group relative flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 transition-colors',
        isSelected
          ? 'bg-sidebar-accent text-foreground'
          : 'text-foreground hover:bg-muted/60',
      )}
      onClick={() => navigate(`/projects/${program.program_id}/tasks`)}
    >
      {/* Active indicator */}
      {isSelected && (
        <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary" />
      )}

      <FolderOpen
        className={cn(
          'size-4 shrink-0',
          isSelected ? 'text-primary' : 'text-muted-foreground/50',
        )}
      />

      <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
        {program.title}
      </span>

      {/* Unread dot — only when there are unseen completed tasks */}
      {hasUnread && !isSelected && (
        <span className="size-2 shrink-0 rounded-full bg-emerald-500" />
      )}

      {/* Actions (visible on hover or when selected) */}
      <div
        className={cn(
          'shrink-0 transition-opacity',
          isSelected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex size-5 items-center justify-center rounded text-muted-foreground/60 hover:bg-muted hover:text-foreground"
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-36">
            {isArchived ? (
              <DropdownMenuItem
                onClick={() =>
                  updateStatus.mutate({
                    programId: program.program_id,
                    status: 'active',
                  })
                }
              >
                <RotateCcw className="size-3.5" />
                {t('projects.reactivate')}
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem
                onClick={() =>
                  updateStatus.mutate({
                    programId: program.program_id,
                    status: 'archived',
                  })
                }
                className="text-destructive focus:text-destructive"
              >
                <Archive className="size-3.5" />
                {t('projects.archive')}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

// Project Tasks tab -- split-pane layout reusing ControlCenter pattern, scoped to project.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Send, Loader2, Terminal, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { TaskCardGrid } from '@/components/control/TaskCardGrid';
import { TaskDetailPanel } from '@/components/control/TaskDetailPanel';
import { CreateTeamDialog } from '@/components/teams/CreateTeamDialog';
import { useProgramTasks, useSubmitProgramTask, useTeamList } from '@/api/hooks';
import { useTaskStream } from '@/hooks/useTaskStream';

const ARCHIVED_STORAGE_KEY = 'hermit-project-archived-tasks';

const POLICY_PROFILES = ['autonomous', 'supervised', 'default'] as const;
type PolicyProfile = (typeof POLICY_PROFILES)[number];

const MAX_ROWS = 3;
const LINE_HEIGHT_PX = 24;

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

export default function ProjectTasks() {
  const { t } = useTranslation();
  const { programId } = useParams<{ programId: string }>();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [archivedIds, setArchivedIds] = useState<Set<string>>(loadArchivedIds);

  // Task input state
  const [description, setDescription] = useState('');
  const [policy, setPolicy] = useState<PolicyProfile>('autonomous');
  const [selectedTeamId, setSelectedTeamId] = useState<string>('__none__');
  const [showSuccess, setShowSuccess] = useState(false);
  const [createTeamOpen, setCreateTeamOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // SSE for live updates
  useTaskStream();

  // Fetch tasks scoped to this program
  const { data } = useProgramTasks(programId ?? '', undefined, 100);
  const tasks = data?.tasks ?? [];

  // Fetch teams for this program
  const { data: teamsData } = useTeamList(programId);
  const teams = teamsData?.teams ?? [];

  const submitMutation = useSubmitProgramTask();

  const selectedTask = tasks.find((t) => t.task_id === selectedTaskId) ?? null;

  // Persist archived IDs
  useEffect(() => {
    localStorage.setItem(ARCHIVED_STORAGE_KEY, JSON.stringify([...archivedIds]));
  }, [archivedIds]);

  const handleArchive = useCallback((id: string) => {
    setArchivedIds((prev) => new Set([...prev, id]));
  }, []);

  const handleSelect = useCallback((id: string) => {
    setSelectedTaskId((prev) => (prev === id ? null : id));
  }, []);

  // Input handlers
  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = MAX_ROWS * LINE_HEIGHT_PX;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  const handleSubmit = useCallback(async () => {
    const trimmed = description.trim();
    if (!trimmed || submitMutation.isPending || !programId) return;

    const teamId = selectedTeamId === '__none__' ? undefined : selectedTeamId;

    await submitMutation.mutateAsync({
      programId,
      description: trimmed,
      policy_profile: policy,
      team_id: teamId,
    });

    setDescription('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    setShowSuccess(true);
    setTimeout(() => setShowSuccess(false), 1500);
  }, [description, policy, programId, selectedTeamId, submitMutation]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setDescription(e.target.value);
      adjustHeight();
    },
    [adjustHeight],
  );

  const isSubmitDisabled = submitMutation.isPending || !description.trim();

  return (
    <div className="flex h-full gap-0">
      {/* Left Panel: Task List */}
      <div className="flex w-1/2 flex-col border-r border-border/50 overflow-hidden">
        {/* Task Input — compact inline bar */}
        <div className="shrink-0 border-b border-border/50 px-4 py-3" data-tour-id="task-input">
          <div className="flex items-end gap-2">
            <div className="flex-1 min-w-0">
              <textarea
                ref={textareaRef}
                value={description}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                placeholder={t('controlCenter.inputPlaceholder')}
                disabled={submitMutation.isPending}
                rows={1}
                className="w-full resize-none bg-transparent text-sm leading-6 text-foreground placeholder:text-muted-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50"
              />
              <div className="flex items-center gap-2 mt-1" data-tour-id="policy-selector">
                <Select value={policy} onValueChange={(v) => setPolicy(v as PolicyProfile)}>
                  <SelectTrigger size="sm" className="h-6 min-w-[100px] text-[11px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {POLICY_PROFILES.map((p) => (
                      <SelectItem key={p} value={p}>
                        {t(`controlCenter.policyOptions.${p}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={selectedTeamId}
                  onValueChange={(v) => {
                    if (v === '__create__') {
                      setCreateTeamOpen(true);
                    } else {
                      setSelectedTeamId(v);
                    }
                  }}
                >
                  <SelectTrigger size="sm" className="h-6 min-w-[100px] text-[11px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('projects.noTeam')}</SelectItem>
                    {teams.map((team) => (
                      <SelectItem key={team.team_id} value={team.team_id}>
                        {team.title}
                      </SelectItem>
                    ))}
                    <SelectItem value="__create__" className="text-primary">
                      {t('projects.createTeamInline')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <Button
              size="sm"
              disabled={isSubmitDisabled && !showSuccess}
              onClick={handleSubmit}
              className={cn(
                'h-8 px-3 shrink-0 transition-colors duration-200',
                showSuccess && 'bg-emerald-500 hover:bg-emerald-500 text-white',
              )}
            >
              {submitMutation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : showSuccess ? (
                <Check className="size-4" />
              ) : (
                <Send className="size-3.5" />
              )}
            </Button>
          </div>
        </div>

        {/* Task Cards - scrollable */}
        <div className="flex-1 overflow-y-auto p-4 pt-2" data-tour-id="task-card-area">
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

      {/* Create Team Dialog */}
      <CreateTeamDialog
        open={createTeamOpen}
        onOpenChange={setCreateTeamOpen}
        defaultProgramId={programId}
      />
    </div>
  );
}

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  useTaskSteps,
  useTaskReceipts,
  useApprovals,
  useCancelTask,
  useTaskOutput,
} from "@/api/hooks";
import { formatTimeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Loader2, RotateCcw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { StepPills } from "@/components/control/StepPills";
import { InlineApproval } from "@/components/control/InlineApproval";
import type { TaskRecord, StepRecord, ReceiptRecord } from "@/types";

// ---------------------------------------------------------------------------
// Status-based styling
// ---------------------------------------------------------------------------

const STATUS_BORDER: Record<string, string> = {
  running: "border-l-[3px] border-l-primary",
  blocked: "border-l-[3px] border-l-amber-500",
  completed: "border-l-[3px] border-l-emerald-500",
  failed: "border-l-[3px] border-l-rose-500",
  queued: "border-l-[3px] border-l-stone-400 dark:border-l-stone-600",
  cancelled: "border-l-[3px] border-l-stone-300 dark:border-l-stone-700",
};

const STATUS_DOT: Record<string, { color: string; pulse?: boolean }> = {
  running: { color: "bg-primary", pulse: true },
  blocked: { color: "bg-amber-500", pulse: false },
  completed: { color: "bg-emerald-500" },
  failed: { color: "bg-rose-500" },
  queued: { color: "bg-stone-400 dark:bg-stone-600" },
  cancelled: { color: "bg-stone-300 dark:bg-stone-500" },
};

const PROGRESS_BAR_COLOR: Record<string, string> = {
  running: "bg-primary",
  blocked: "bg-amber-500",
  completed: "bg-emerald-500",
  failed: "bg-rose-500",
  queued: "bg-stone-400",
  cancelled: "bg-stone-300",
};

function getStatusBorder(status: string): string {
  return STATUS_BORDER[status] ?? "border-l-[3px] border-l-stone-400";
}

function getStatusDot(status: string) {
  return STATUS_DOT[status] ?? { color: "bg-stone-400" };
}

function getProgressColor(status: string): string {
  return PROGRESS_BAR_COLOR[status] ?? "bg-stone-400";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(startedAt: number | null): string {
  if (!startedAt) return "";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - startedAt));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours}h${mins > 0 ? `${mins}m` : ""}`;
}

const ACTIVE_STATUSES = new Set(["running", "queued", "blocked"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TaskCardProps {
  readonly task: TaskRecord;
  readonly selected: boolean;
  readonly onSelect: () => void;
  readonly onArchive: () => void;
}

export function TaskCard({
  task,
  selected,
  onSelect,
  onArchive: _onArchive,
}: TaskCardProps) {
  const { t } = useTranslation();

  const { data: stepsData } = useTaskSteps(task.task_id);
  const { data: receiptsData } = useTaskReceipts(task.task_id);
  const { data: approvalsData } = useApprovals("pending", 50);
  const { data: outputData } = useTaskOutput(task.task_id);

  const cancelMutation = useCancelTask();

  const steps: ReadonlyArray<StepRecord> = stepsData?.steps ?? [];
  const receipts: ReadonlyArray<ReceiptRecord> = receiptsData?.receipts ?? [];

  const isTerminal = TERMINAL_STATUSES.has(task.status);

  const taskApprovals = useMemo(
    () =>
      (approvalsData?.approvals ?? []).filter(
        (a) => a.task_id === task.task_id,
      ),
    [approvalsData, task.task_id],
  );

  // Progress
  const completedSteps = steps.filter((s) => s.status === "completed").length;
  const totalSteps = steps.length;
  const progressPercent =
    totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;

  // Goal truncation
  const goalPreview =
    task.goal.length > 100 ? `${task.goal.slice(0, 100)}...` : task.goal;

  // Duration
  const duration = formatDuration(task.started_at);

  // Rollback eligibility
  const hasRollbackReceipts = receipts.some((r) => r.rollback_supported);

  const dot = getStatusDot(task.status);
  const isActive = ACTIVE_STATUSES.has(task.status);

  // Action handlers
  function handleCancel(e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm(t("control.actions.cancelConfirm"))) return;
    cancelMutation.mutate({ taskId: task.task_id });
  }

  // Last 3 output receipts for compact display
  const outputReceipts = outputData?.receipts ?? [];
  const outputSummary = isTerminal ? outputReceipts.slice(-3) : [];

  return (
    <TooltipProvider>
      <div
        className={cn(
          "overflow-hidden rounded-xl bg-card shadow-sm ring-1 ring-border/50 transition-all hover:shadow-md",
          getStatusBorder(task.status),
          selected && "ring-2 ring-primary shadow-md",
          task.status === "completed" && !selected && "opacity-90",
        )}
      >
        {/* Collapsed summary -- always visible */}
        <button
          type="button"
          onClick={onSelect}
          className="w-full cursor-pointer px-3 py-2.5 text-left focus:outline-none"
        >
          {/* Row 1: status dot + title + progress% + action buttons */}
          <div className="flex items-center gap-2">
            {/* Status dot */}
            <span className="relative flex size-2 flex-shrink-0">
              {dot.pulse && (
                <span
                  className={cn(
                    "absolute inline-flex size-full animate-ping rounded-full opacity-40",
                    dot.color,
                  )}
                />
              )}
              <span
                className={cn(
                  "relative inline-flex size-2 rounded-full",
                  dot.color,
                )}
              />
            </span>

            {/* Title */}
            <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
              {task.title}
            </span>

            {/* Progress % */}
            {totalSteps > 0 && (
              <span className="flex-shrink-0 text-xs font-medium tabular-nums text-muted-foreground">
                {progressPercent}%
              </span>
            )}

            {/* Action buttons -- icon only */}
            <div
              className="flex flex-shrink-0 items-center gap-1"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {/* Cancel: active tasks only */}
              {isActive && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={handleCancel}
                      disabled={cancelMutation.isPending}
                      className="inline-flex size-5 items-center justify-center rounded text-muted-foreground/60 transition-colors hover:bg-muted hover:text-red-500 disabled:opacity-50"
                    >
                      {cancelMutation.isPending ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <X className="size-3" />
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span>{t("control.actions.cancel")}</span>
                  </TooltipContent>
                </Tooltip>
              )}

              {/* Rollback indicator: terminal tasks with rollback receipts */}
              {isTerminal && hasRollbackReceipts && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex size-5 items-center justify-center rounded text-muted-foreground/40">
                      <RotateCcw className="size-3" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span>{t("control.actions.rollback")}</span>
                  </TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>

          {/* Row 2: goal -- single line truncate */}
          <p className="mt-1 truncate text-xs text-muted-foreground/80">
            {goalPreview}
          </p>

          {/* Row 3: step dots + metadata inline */}
          {steps.length > 0 && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground/60">
              <StepPills steps={[...steps]} />
              <span className="mx-0.5">&middot;</span>
              <span className="tabular-nums">
                {completedSteps}/{totalSteps} {t("control.taskCard.steps")}
              </span>
              {receipts.length > 0 && (
                <>
                  <span className="mx-0.5">&middot;</span>
                  <span className="tabular-nums">
                    {receipts.length} {t("control.taskCard.receipts")}
                  </span>
                </>
              )}
              {duration && (
                <>
                  <span className="mx-0.5">&middot;</span>
                  <span className="tabular-nums">{duration}</span>
                </>
              )}
            </div>
          )}

          {/* Row 4: progress bar + policy + time */}
          <div className="mt-1.5 flex items-center gap-2">
            {totalSteps > 0 && (
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    "h-full rounded-full transition-all duration-500",
                    getProgressColor(task.status),
                  )}
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            )}
            {totalSteps === 0 && <div className="flex-1" />}
            <div className="flex flex-shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground/60">
              <Badge variant="outline" className="h-4 px-1 text-[10px]">
                {task.policy_profile}
              </Badge>
              <span>&middot;</span>
              <span className="tabular-nums">
                {formatTimeAgo(task.updated_at)}
              </span>
            </div>
          </div>

          {/* Row 5 (conditional): Output summary -- compact receipt list */}
          {isTerminal && outputSummary.length > 0 && (
            <div className="mt-1.5 truncate text-[11px] text-muted-foreground/50">
              <span className="font-medium text-muted-foreground/70">
                {t("control.output.title")}:
              </span>{" "}
              {outputSummary.map((r, i) => (
                <span key={r.receipt_id ?? i}>
                  {i > 0 && " \u00b7 "}
                  {r.action_type ?? "?"}
                  <span className="mx-0.5">&rarr;</span>
                  {r.result_code ?? "?"}
                </span>
              ))}
            </div>
          )}

          {/* Row 6 (conditional): Response text -- LLM reply preview */}
          {outputData?.response_text && (
            <div className="mt-1 rounded-lg bg-muted/50 px-2.5 py-1.5">
              <p className="line-clamp-3 text-xs text-foreground/80 whitespace-pre-wrap">
                {outputData.response_text}
              </p>
            </div>
          )}
        </button>

        {/* Inline approvals when blocked */}
        {task.status === "blocked" && taskApprovals.length > 0 && (
          <div className="space-y-1.5 border-t border-border/50 px-3 pb-2.5 pt-2">
            {taskApprovals.map((approval) => (
              <InlineApproval
                key={approval.approval_id}
                approval={approval}
              />
            ))}
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

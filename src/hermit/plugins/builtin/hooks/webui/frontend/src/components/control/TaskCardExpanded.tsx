import { useTranslation } from "react-i18next";
import {
  CheckCircle,
  Circle,
  Clock,
  FileText,
  Loader2,
  RotateCcw,
  X,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useTaskSteps,
  useTaskReceipts,
  useApprovals,
  useCancelTask,
  useRollbackReceipt,
  useTaskOutput,
} from "@/api/hooks";
import { InlineApproval } from "@/components/control/InlineApproval";
import type { TaskRecord, StepRecord } from "@/types";

// ---------------------------------------------------------------------------
// Step icon config
// ---------------------------------------------------------------------------

const STEP_ICON_CONFIG: Record<
  string,
  { icon: typeof Circle; color: string; animate?: boolean }
> = {
  completed: {
    icon: CheckCircle,
    color: "text-emerald-500 dark:text-emerald-400",
  },
  running: {
    icon: Circle,
    color: "text-primary",
    animate: true,
  },
  blocked: {
    icon: Clock,
    color: "text-amber-500 dark:text-amber-400",
  },
  pending: {
    icon: Circle,
    color: "text-muted-foreground/40",
  },
  failed: {
    icon: XCircle,
    color: "text-rose-500 dark:text-rose-400",
  },
};

function getStepIcon(status: string) {
  return (
    STEP_ICON_CONFIG[status] ?? {
      icon: Circle,
      color: "text-muted-foreground/40",
    }
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(
  startedAt: number | null,
  finishedAt: number | null,
): string | null {
  if (!startedAt) return null;
  const end = finishedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - startedAt));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${mins}m`;
}

const ACTIVE_STATUSES = new Set(["running", "queued", "blocked"]);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TaskCardExpandedProps {
  readonly task: TaskRecord;
}

export function TaskCardExpanded({ task }: TaskCardExpandedProps) {
  const { t } = useTranslation();

  const isActive = ACTIVE_STATUSES.has(task.status);

  const { data: stepsData, isLoading: stepsLoading } = useTaskSteps(
    task.task_id,
  );
  const { data: receiptsData } = useTaskReceipts(task.task_id);
  const { data: approvalsData } = useApprovals("pending", 50);
  const { data: outputData } = useTaskOutput(task.task_id);

  const cancelMutation = useCancelTask();
  const rollbackMutation = useRollbackReceipt();

  const steps: ReadonlyArray<StepRecord> = stepsData?.steps ?? [];
  const receipts = receiptsData?.receipts ?? [];
  const taskApprovals = (approvalsData?.approvals ?? []).filter(
    (a) => a.task_id === task.task_id,
  );

  const rollbackReceipts = receipts.filter((r) => r.rollback_supported);
  const rollbackCount = rollbackReceipts.length;

  function handleCancel() {
    if (!window.confirm(t("control.actions.cancelConfirm"))) return;
    cancelMutation.mutate({ taskId: task.task_id });
  }

  function handleRollbackAll() {
    if (!window.confirm(t("control.actions.rollbackAllConfirm"))) return;
    for (const receipt of rollbackReceipts) {
      rollbackMutation.mutate(receipt.receipt_id);
    }
  }

  function handleRollbackSingle(receiptId: string) {
    if (!window.confirm(t("control.actions.rollbackConfirm"))) return;
    rollbackMutation.mutate(receiptId);
  }

  return (
    <div className="space-y-4">
      {/* Goal */}
      <div>
        <p className="text-sm text-foreground/80">{task.goal}</p>
      </div>

      {/* Step Timeline */}
      {stepsLoading ? (
        <div className="space-y-1.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-6 animate-pulse rounded bg-muted"
            />
          ))}
        </div>
      ) : steps.length === 0 ? (
        <div className="flex items-center justify-center py-2 text-xs text-muted-foreground">
          <Circle className="mr-1.5 size-3 text-muted-foreground/40" />
          {t("taskDetail.steps.noSteps")}
        </div>
      ) : (
        <div className="relative space-y-0 pl-0.5">
          {steps.map((step, index) => {
            const config = getStepIcon(step.status);
            const Icon = config.icon;
            const duration = formatDuration(step.started_at, step.finished_at);
            const isLast = index === steps.length - 1;
            const stepApprovals = taskApprovals.filter(
              (a) => a.step_id === step.step_id,
            );

            return (
              <div key={step.step_id} className="relative flex gap-2 pb-3">
                {/* Connecting line */}
                {!isLast && (
                  <div className="absolute left-[7px] top-4 h-[calc(100%-8px)] w-px bg-gradient-to-b from-border to-border/30" />
                )}

                {/* Icon */}
                <div className="relative z-10 flex-shrink-0 pt-0.5">
                  <div
                    className={cn(
                      "flex size-4 items-center justify-center",
                      config.animate && "animate-pulse",
                    )}
                  >
                    <Icon className={cn("size-3.5", config.color)} />
                  </div>
                </div>

                {/* Content */}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-medium text-foreground">
                      {step.title ?? step.kind}
                    </span>
                    {step.attempt > 1 && (
                      <span className="rounded-full border border-border px-1 py-px text-[10px] text-muted-foreground">
                        {t("taskDetail.steps.attempt", {
                          number: step.attempt,
                        })}
                      </span>
                    )}
                    {duration && (
                      <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground/60">
                        <Clock className="size-2" />
                        {duration}
                      </span>
                    )}
                  </div>

                  {/* Inline approvals for blocked steps */}
                  {stepApprovals.length > 0 && (
                    <div className="mt-1.5 space-y-1">
                      {stepApprovals.map((approval) => (
                        <InlineApproval
                          key={approval.approval_id}
                          approval={approval}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Output section -- all receipt summaries with result_summary */}
      {receipts.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <FileText className="size-3" />
            {t("control.expanded.output")} ({receipts.length})
          </div>
          <div className="space-y-1 rounded-xl bg-muted/50 px-3 py-2">
            {receipts.map((receipt) => (
              <div
                key={receipt.receipt_id}
                className="flex items-start gap-1.5 text-[11px]"
              >
                <span className="flex-shrink-0 font-mono text-muted-foreground/70">
                  {receipt.action_type}
                </span>
                <span className="flex-shrink-0 text-muted-foreground/40">&rarr;</span>
                <span
                  className={cn(
                    "flex-shrink-0 font-medium",
                    receipt.result_code === "succeeded"
                      ? "text-emerald-600 dark:text-emerald-400"
                      : receipt.result_code === "failed"
                        ? "text-rose-600 dark:text-rose-400"
                        : "text-muted-foreground",
                  )}
                >
                  {receipt.result_code}
                </span>
                {receipt.result_summary && (
                  <span className="min-w-0 flex-1 text-muted-foreground/60">
                    {receipt.result_summary}
                  </span>
                )}
                {receipt.rollback_supported && (
                  <button
                    type="button"
                    onClick={() => handleRollbackSingle(receipt.receipt_id)}
                    disabled={rollbackMutation.isPending}
                    className="ml-auto flex-shrink-0 text-muted-foreground/40 transition-colors hover:text-amber-500"
                  >
                    <RotateCcw className="size-2.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No output placeholder */}
      {receipts.length === 0 && !outputData?.response_text && (
        <div className="text-[11px] text-muted-foreground/50">
          {t("control.expanded.noOutput")}
        </div>
      )}

      {/* Inline approvals not attached to a specific step */}
      {taskApprovals.filter((a) => !a.step_id).length > 0 && (
        <div className="space-y-1">
          {taskApprovals
            .filter((a) => !a.step_id)
            .map((approval) => (
              <InlineApproval
                key={approval.approval_id}
                approval={approval}
              />
            ))}
        </div>
      )}

      {/* Action buttons row */}
      <div className="flex items-center gap-2 pt-0.5">
        {/* Cancel Task -- active tasks */}
        {isActive && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelMutation.isPending}
            className="inline-flex h-6 items-center gap-1 rounded-md border border-border px-2 text-[11px] font-medium text-muted-foreground transition-all hover:border-red-300 hover:text-red-500 disabled:opacity-50 dark:hover:border-red-700"
          >
            {cancelMutation.isPending ? (
              <Loader2 className="size-2.5 animate-spin" />
            ) : (
              <X className="size-2.5" />
            )}
            {t("control.expanded.cancelTask")}
          </button>
        )}

        {/* Rollback All -- terminal tasks with rollback receipts */}
        {!isActive && rollbackCount > 0 && (
          <button
            type="button"
            onClick={handleRollbackAll}
            disabled={rollbackMutation.isPending}
            className="inline-flex h-6 items-center gap-1 rounded-md border border-border px-2 text-[11px] font-medium text-muted-foreground transition-all hover:border-amber-300 hover:text-amber-600 disabled:opacity-50 dark:hover:border-amber-700 dark:hover:text-amber-400"
          >
            {rollbackMutation.isPending ? (
              <Loader2 className="size-2.5 animate-spin" />
            ) : (
              <RotateCcw className="size-2.5" />
            )}
            {t("control.expanded.rollbackAll")} ({rollbackCount})
          </button>
        )}
      </div>
    </div>
  );
}

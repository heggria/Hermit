import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle,
  Circle,
  Clock,
  Loader2,
  MinusCircle,
  RotateCcw,
  SkipForward,
  X,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getStatusStyle } from "@/lib/status-styles";
import {
  useTaskSteps,
  useTaskReceipts,
  useApprovals,
  useCancelTask,
  useRollbackReceipt,
  useToolCalls,
} from "@/api/hooks";
import { InlineApproval } from "@/components/control/InlineApproval";
import type { TaskRecord, StepRecord, ReceiptRecord, ToolCallRecord } from "@/types";

// ---------------------------------------------------------------------------
// Step icon config — icons are component-specific, colors come from central styles
// ---------------------------------------------------------------------------

const STEP_ICON_MAP: Record<string, typeof Circle> = {
  completed: CheckCircle,
  succeeded: CheckCircle,
  failed: XCircle,
  blocked: Clock,
  awaiting_approval: Clock,
  waiting: Clock,
  skipped: SkipForward,
  superseded: MinusCircle,
};

function getStepIcon(status: string) {
  const style = getStatusStyle(status);
  return {
    icon: STEP_ICON_MAP[status] ?? Circle,
    color: style.text,
    animate: style.pulse ?? false,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const RUNNING_STATUSES = new Set([
  "running",
  "dispatching",
  "contracting",
  "preflighting",
]);

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

/** Group receipts by their step_id. */
function groupReceiptsByStep(
  receipts: ReadonlyArray<ReceiptRecord>,
): Map<string, ReceiptRecord[]> {
  const map = new Map<string, ReceiptRecord[]>();
  for (const receipt of receipts) {
    if (!receipt.step_id) continue;
    const existing = map.get(receipt.step_id);
    if (existing) {
      existing.push(receipt);
    } else {
      map.set(receipt.step_id, [receipt]);
    }
  }
  return map;
}

/** Group tool calls by their step_id. */
function groupToolCallsByStep(
  toolCalls: ReadonlyArray<ToolCallRecord>,
): Map<string, ToolCallRecord[]> {
  const map = new Map<string, ToolCallRecord[]>();
  for (const tc of toolCalls) {
    if (!tc.step_id) continue;
    const existing = map.get(tc.step_id);
    if (existing) {
      existing.push(tc);
    } else {
      map.set(tc.step_id, [tc]);
    }
  }
  return map;
}

// ---------------------------------------------------------------------------
// Live duration hook — ticks every second while any step is running
// ---------------------------------------------------------------------------

function useLiveTick(steps: ReadonlyArray<StepRecord>): number {
  const [tick, setTick] = useState(0);
  const hasRunning = steps.some((s) => RUNNING_STATUSES.has(s.status));

  useEffect(() => {
    if (!hasRunning) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [hasRunning]);

  return tick;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TaskCardExpandedProps {
  readonly task: TaskRecord;
}

export function TaskCardExpanded({ task }: TaskCardExpandedProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const isActive = ACTIVE_STATUSES.has(task.status);

  // Active tool call (set via SSE tool.active event)
  const activeTool = isActive
    ? (queryClient.getQueryData<{ tool_name: string; input_summary: string }>(
        ["tasks", task.task_id, "active-tool"],
      ) ?? null)
    : null;

  const { data: stepsData, isLoading: stepsLoading } = useTaskSteps(
    task.task_id,
    isActive,
  );
  const { data: receiptsData } = useTaskReceipts(task.task_id, isActive);
  const { data: toolCallsData } = useToolCalls(task.task_id, isActive);
  const { data: approvalsData } = useApprovals("pending", 50);

  const cancelMutation = useCancelTask();
  const rollbackMutation = useRollbackReceipt();

  const steps: ReadonlyArray<StepRecord> = stepsData?.steps ?? [];
  const receipts = receiptsData?.receipts ?? [];
  const toolCalls = toolCallsData?.tool_calls ?? [];
  const taskApprovals = (approvalsData?.approvals ?? []).filter(
    (a) => a.task_id === task.task_id,
  );

  const receiptsByStep = groupReceiptsByStep(receipts);
  const toolCallsByStep = groupToolCallsByStep(toolCalls);

  const rollbackReceipts = receipts.filter((r) => r.rollback_supported);
  const rollbackCount = rollbackReceipts.length;

  // Live-tick so running step durations update every second
  useLiveTick(steps);

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
    <div className="space-y-3" data-tour-id="step-timeline">
      {/* Goal -- only if different from title */}
      {task.goal && task.goal !== task.title && (
        <div>
          <p className="text-sm text-foreground/80">{task.goal}</p>
        </div>
      )}

      {/* Step Timeline with inline receipts */}
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
            const isStepRunning = RUNNING_STATUSES.has(step.status);
            const duration = formatDuration(
              step.started_at,
              isStepRunning ? null : step.finished_at,
            );
            const isLast = index === steps.length - 1;
            const stepApprovals = taskApprovals.filter(
              (a) => a.step_id === step.step_id,
            );
            const stepReceipts = receiptsByStep.get(step.step_id) ?? [];
            const stepToolCalls = toolCallsByStep.get(step.step_id) ?? [];
            // Show tool calls when available; fall back to receipts
            const hasToolCalls = stepToolCalls.length > 0;
            const hasReceipts = stepReceipts.length > 0;

            return (
              <div key={step.step_id} className="relative flex gap-2 pb-3">
                {/* Connecting line */}
                {!isLast && (
                  <div className="absolute left-[7px] top-4 h-[calc(100%-8px)] w-px bg-gradient-to-b from-border to-border/30" />
                )}

                {/* Icon */}
                <div className="relative z-10 flex size-4 flex-shrink-0 items-center justify-center">
                  <Icon
                    className={cn(
                      "size-3.5",
                      config.color,
                      config.animate && "animate-pulse",
                    )}
                  />
                </div>

                {/* Content */}
                <div className="min-w-0 flex-1">
                  {/* Step header */}
                  <div className="flex flex-wrap items-center gap-1.5 leading-4">
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
                      <span className={cn(
                        "flex items-center gap-0.5 text-[10px]",
                        isStepRunning
                          ? "text-primary tabular-nums"
                          : "text-muted-foreground/60",
                      )}>
                        <Clock className="size-2" />
                        {duration}
                      </span>
                    )}
                  </div>

                  {/* Inline tool calls — shows all tool invocations including read-only ones.
                      Falls back to receipts when tool-calls endpoint unavailable. */}
                  {hasToolCalls ? (
                    <div className="mt-1 space-y-0.5">
                      {stepToolCalls.map((tc, idx) => (
                        <div
                          key={`${tc.tool_name}-${tc.occurred_at ?? idx}`}
                          className="flex items-center gap-1.5 text-[11px]"
                        >
                          <span
                            className={cn(
                              "size-1.5 shrink-0 rounded-full",
                              tc.result_code === "succeeded"
                                ? "bg-emerald-500"
                                : tc.result_code === "failed"
                                  ? "bg-rose-500"
                                  : tc.has_receipt
                                    ? "bg-muted-foreground/40"
                                    : "bg-sky-400",
                            )}
                          />
                          <span className="shrink-0 font-medium text-foreground/60">
                            {tc.tool_name}
                          </span>
                          <span
                            className="min-w-0 flex-1 truncate font-mono text-muted-foreground/60"
                            title={tc.action_label || tc.action_class || ""}
                          >
                            {tc.action_label || tc.action_class || ""}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : hasReceipts ? (
                    <div className="mt-1 space-y-0.5">
                      {stepReceipts.map((receipt) => (
                        <div
                          key={receipt.receipt_id}
                          className="flex items-center gap-1.5 text-[11px]"
                        >
                          <span
                            className={cn(
                              "size-1.5 shrink-0 rounded-full",
                              receipt.result_code === "succeeded"
                                ? "bg-emerald-500"
                                : receipt.result_code === "failed"
                                  ? "bg-rose-500"
                                  : "bg-muted-foreground/40",
                            )}
                          />
                          <span className="shrink-0 font-medium text-foreground/60">
                            {receipt.action_type}
                          </span>
                          <span
                            className="min-w-0 flex-1 truncate font-mono text-muted-foreground/60"
                            title={receipt.action_label || receipt.result_summary || ""}
                          >
                            {receipt.action_label || receipt.result_summary || ""}
                          </span>
                          {receipt.rollback_supported && (
                            <button
                              type="button"
                              onClick={() =>
                                handleRollbackSingle(receipt.receipt_id)
                              }
                              disabled={rollbackMutation.isPending}
                              className="ml-auto shrink-0 text-muted-foreground/40 transition-colors hover:text-amber-500"
                            >
                              <RotateCcw className="size-2.5" />
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : null}

                  {/* Running indicator when step is active */}
                  {isStepRunning && (
                    <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground/60">
                      <Loader2 className="size-2.5 animate-spin shrink-0" />
                      {activeTool ? (
                        <>
                          <span className="shrink-0 font-medium">{activeTool.tool_name}</span>
                          {activeTool.input_summary && (
                            <span className="min-w-0 truncate font-mono text-muted-foreground/40">
                              {activeTool.input_summary}
                            </span>
                          )}
                        </>
                      ) : (stepReceipts.length === 0 && stepToolCalls.length === 0) ? (
                        <span>{t("taskDetail.steps.executing")}</span>
                      ) : null}
                    </div>
                  )}

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

      {/* Action buttons row — only render when there are buttons */}
      {(isActive || rollbackCount > 0) && (
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
      )}
    </div>
  );
}

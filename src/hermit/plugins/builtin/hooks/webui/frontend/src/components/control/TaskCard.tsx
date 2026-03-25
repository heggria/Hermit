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
import { Archive, Loader2, RotateCcw, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { InlineApproval } from "@/components/control/InlineApproval";
import { getStatusStyle } from "@/lib/status-styles";
import type { TaskRecord, StepRecord, ReceiptRecord } from "@/types";

const STEP_TERMINAL = new Set(["completed", "succeeded", "done", "failed", "cancelled", "skipped", "superseded"]);
const ACTIVE_STATUSES = new Set(["running", "queued", "blocked"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

function formatDuration(startedAt: number | null, endedAt: number | null): string {
  if (!startedAt) return "";
  const end = endedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - startedAt));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours}h${mins > 0 ? `${mins}m` : ""}`;
}

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
  onArchive,
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
  const isActive = ACTIVE_STATUSES.has(task.status);

  const taskApprovals = useMemo(
    () =>
      (approvalsData?.approvals ?? []).filter(
        (a) => a.task_id === task.task_id,
      ),
    [approvalsData, task.task_id],
  );

  const completedSteps = steps.filter((s) => STEP_TERMINAL.has(s.status)).length;
  const totalSteps = steps.length;

  const statusStyle = getStatusStyle(task.status);
  const dot = { color: statusStyle.dot, pulse: statusStyle.pulse };
  const duration = formatDuration(task.started_at, isTerminal ? (task.finished_at ?? task.updated_at) : null);
  const hasRollbackReceipts = receipts.some((r) => r.rollback_supported);

  const outputReceipts = outputData?.receipts ?? [];
  const outputSummary = isTerminal ? outputReceipts.slice(-3) : [];

  function handleCancel(e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm(t("control.actions.cancelConfirm"))) return;
    cancelMutation.mutate({ taskId: task.task_id });
  }

  function handleArchive(e: React.MouseEvent) {
    e.stopPropagation();
    onArchive();
  }

  return (
    <TooltipProvider>
      <div
        className={cn(
          "relative overflow-hidden rounded-xl bg-card ring-1 ring-border/60 transition-all hover:ring-border",
          selected && "ring-border bg-primary/[0.02]",
        )}
      >
        <button
          type="button"
          onClick={onSelect}
          className="w-full cursor-pointer px-4 py-3 text-left focus:outline-none"
        >
          {/* Selected indicator — left accent bar */}
          {selected && (
            <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-full bg-primary" />
          )}
          {/* Row 1: Title + time */}
          <div className="flex items-start gap-2">
            {/* Status dot */}
            <span className="relative mt-1.5 flex size-2 shrink-0">
              {dot.pulse && (
                <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-40", dot.color)} />
              )}
              <span className={cn("relative inline-flex size-2 rounded-full", dot.color)} />
            </span>

            <div className="min-w-0 flex-1">
              <span className="text-sm font-semibold text-foreground leading-snug line-clamp-1">
                {task.title}
              </span>
            </div>

            <span className="shrink-0 text-xs text-muted-foreground/60 tabular-nums">
              {formatTimeAgo(task.updated_at)}
            </span>
          </div>

          {/* Row 2: Goal text (if different from title) */}
          {task.goal && task.goal !== task.title && (
            <p className="mt-1 ml-4 truncate text-xs text-muted-foreground">
              {task.goal}
            </p>
          )}

          {/* Row 3: Meta chips — compact inline */}
          <div className="mt-2 ml-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {/* Status label */}
            <span className={cn("font-medium", getStatusStyle(task.status).text)}>
              {t(`common.status.${task.status}`, task.status)}
            </span>

            {/* Steps progress — subtle text only */}
            {totalSteps > 0 && (
              <span className="tabular-nums">
                {completedSteps}/{totalSteps} {t("control.taskCard.steps")}
              </span>
            )}

            {/* Receipts count */}
            {receipts.length > 0 && (
              <span className="tabular-nums">
                {receipts.length} {t("control.taskCard.receipts")}
              </span>
            )}

            {/* Duration */}
            {duration && (
              <span className="tabular-nums">{duration}</span>
            )}

            {/* Policy badge */}
            <span className="rounded-full border border-border px-1.5 py-px text-[10px] text-muted-foreground/70">
              {task.policy_profile}
            </span>

            {/* Actions */}
            <div className="ml-auto flex items-center gap-1" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
              {isActive && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={handleCancel}
                      disabled={cancelMutation.isPending}
                      className="inline-flex size-5 items-center justify-center rounded text-muted-foreground/50 transition-colors hover:bg-muted hover:text-destructive disabled:opacity-50"
                    >
                      {cancelMutation.isPending ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent><span>{t("control.actions.cancel")}</span></TooltipContent>
                </Tooltip>
              )}
              {isTerminal && hasRollbackReceipts && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex size-5 items-center justify-center rounded text-muted-foreground/40">
                      <RotateCcw className="size-3" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent><span>{t("control.actions.rollback")}</span></TooltipContent>
                </Tooltip>
              )}
              {isTerminal && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={handleArchive}
                      className="inline-flex size-5 items-center justify-center rounded text-muted-foreground/40 transition-colors hover:bg-muted hover:text-muted-foreground"
                    >
                      <Archive className="size-3" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent><span>{t("control.actions.archive")}</span></TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>

          {/* Output summary — receipts trail */}
          {isTerminal && outputSummary.length > 0 && (
            <div className="mt-2 ml-4 truncate text-[10px] text-muted-foreground/40">
              {outputSummary.map((r, i) => (
                <span key={r.receipt_id ?? i}>
                  {i > 0 && " \u00b7 "}
                  {r.action_type ?? "?"}&rarr;{r.result_code ?? "?"}
                </span>
              ))}
            </div>
          )}

          {/* Response text */}
          {outputData?.response_text && (
            <div className="mt-2 ml-4 rounded-lg bg-muted/40 px-3 py-2">
              <div className="line-clamp-3 text-xs text-foreground/70 prose prose-xs dark:prose-invert max-w-none prose-headings:text-xs prose-headings:font-semibold prose-headings:my-0.5 prose-p:my-0.5 prose-ul:my-0.5 prose-ol:my-0.5 prose-li:my-0 prose-code:text-[10px] prose-code:before:content-none prose-code:after:content-none">
                <Markdown remarkPlugins={[remarkGfm]}>
                  {outputData.response_text}
                </Markdown>
              </div>
            </div>
          )}
        </button>

        {/* Inline approvals when blocked */}
        {task.status === "blocked" && taskApprovals.length > 0 && (
          <div className="space-y-1.5 border-t border-border/50 px-4 pb-3 pt-2">
            {taskApprovals.map((approval) => (
              <InlineApproval key={approval.approval_id} approval={approval} />
            ))}
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

// Right-side detail panel for a selected task in the split-pane Control Center.

import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTaskOutput } from "@/api/hooks";
import { TaskCardExpanded } from "@/components/control/TaskCardExpanded";
import { DrawerChat } from "@/components/control/DrawerChat";
import type { TaskRecord } from "@/types";

// ---------------------------------------------------------------------------
// Status dot styling (mirrors TaskCard)
// ---------------------------------------------------------------------------

const STATUS_DOT: Record<string, { color: string; pulse?: boolean }> = {
  running: { color: "bg-primary", pulse: true },
  blocked: { color: "bg-amber-500", pulse: false },
  completed: { color: "bg-emerald-500" },
  failed: { color: "bg-rose-500" },
  queued: { color: "bg-stone-400 dark:bg-stone-600" },
  cancelled: { color: "bg-stone-300 dark:bg-stone-500" },
};

function getStatusDot(status: string) {
  return STATUS_DOT[status] ?? { color: "bg-stone-400" };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TaskDetailPanelProps {
  readonly task: TaskRecord;
  readonly onClose: () => void;
}

export function TaskDetailPanel({ task, onClose }: TaskDetailPanelProps) {
  const { t } = useTranslation();
  const { data: outputData } = useTaskOutput(task.task_id);
  const dot = getStatusDot(task.status);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-3 border-b border-border/50 px-4 py-3">
        <span className="relative flex size-2.5 flex-shrink-0">
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
              "relative inline-flex size-2.5 rounded-full",
              dot.color,
            )}
          />
        </span>
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {task.title}
        </h2>
        <span className="text-xs font-medium text-muted-foreground">
          {t(`common.status.${task.status}`)}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Scrollable content area */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {/* Response text -- LLM reply */}
        {outputData?.response_text && (
          <div className="mb-4 rounded-xl bg-muted/50 p-3">
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              {t("control.output.response")}
            </p>
            <p className="text-sm text-foreground whitespace-pre-wrap">
              {outputData.response_text}
            </p>
          </div>
        )}

        {/* Step timeline, receipts, actions */}
        <TaskCardExpanded task={task} />
      </div>

      {/* Fixed bottom: DrawerChat */}
      <DrawerChat task={task} />
    </div>
  );
}

// Right-side detail panel for a selected task in the split-pane Control Center.

import { X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { getStatusStyle } from "@/lib/status-styles";
import { useTaskOutput } from "@/api/hooks";
import { TaskCardExpanded } from "@/components/control/TaskCardExpanded";
import { DrawerChat } from "@/components/control/DrawerChat";
import type { TaskRecord } from "@/types";

function getStatusDot(status: string) {
  const style = getStatusStyle(status);
  return { color: style.dot, pulse: style.pulse };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TaskDetailPanelProps {
  readonly task: TaskRecord;
  readonly onClose: () => void;
}

export function TaskDetailPanel({ task, onClose }: TaskDetailPanelProps) {
  const { data: outputData } = useTaskOutput(task.task_id);
  const dot = getStatusDot(task.status);

  return (
    <div className="flex h-full flex-col" data-tour-id="task-detail-panel">
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
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-foreground">
            {task.title}
            {task.goal && task.goal !== task.title && (
              <span className="ml-2 font-normal text-muted-foreground">{task.goal}</span>
            )}
          </h2>
          <p className="truncate text-[10px] font-mono text-muted-foreground/60">{task.task_id}</p>
        </div>
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
        {/* Step timeline, receipts, actions — shown first so tool calls are visible */}
        <TaskCardExpanded task={task} />

        {/* Response text -- LLM reply */}
        {outputData?.response_text && (
          <div className="mt-3 rounded-xl bg-muted/50 p-3">
            <div className="prose prose-sm dark:prose-invert max-w-none text-sm text-foreground prose-headings:text-foreground prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1.5 prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[13px] prose-code:before:content-none prose-code:after:content-none prose-pre:bg-muted prose-pre:rounded-lg prose-a:text-primary">
              <Markdown remarkPlugins={[remarkGfm]}>
                {outputData.response_text}
              </Markdown>
            </div>
          </div>
        )}
      </div>

      {/* Fixed bottom: DrawerChat */}
      <DrawerChat task={task} />
    </div>
  );
}

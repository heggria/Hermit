import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, SendHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSteerTask, useSubmitTask } from "@/api/hooks";
import type { TaskRecord } from "@/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface DrawerChatProps {
  readonly task: TaskRecord;
  readonly onNewTask?: () => void;
}

export function DrawerChat({ task, onNewTask }: DrawerChatProps) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const steerMutation = useSteerTask();
  const submitMutation = useSubmitTask();

  const isTerminal = TERMINAL_STATUSES.has(task.status);
  const isPending = steerMutation.isPending || submitMutation.isPending;

  const showFlash = useCallback((message: string) => {
    setFlash(message);
    setTimeout(() => setFlash(null), 3000);
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isPending) return;

    if (isTerminal) {
      // Continue mode: create a new follow-up task
      const description = `[${t("controlCenter.drawer.followUpPrefix")}: ${task.title}] ${trimmed}`;
      submitMutation.mutate(
        { description, policy_profile: task.policy_profile },
        {
          onSuccess: () => {
            setInput("");
            showFlash(t("controlCenter.drawer.followUpSuccess"));
            onNewTask?.();
          },
        },
      );
    } else {
      // Steer mode: send directive to running task
      steerMutation.mutate(
        { taskId: task.task_id, message: trimmed },
        {
          onSuccess: () => {
            setInput("");
            showFlash(t("controlCenter.drawer.steerSuccess"));
          },
        },
      );
    }
  }, [
    input,
    isPending,
    isTerminal,
    task,
    steerMutation,
    submitMutation,
    onNewTask,
    showFlash,
    t,
  ]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className="border-t border-border/50 bg-card px-4 pb-4 pt-3" data-tour-id="drawer-chat">
      {/* Mode indicator */}
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground/70">
        <span
          className={cn(
            "size-1.5 rounded-full",
            isTerminal ? "bg-stone-400" : "bg-primary",
          )}
        />
        {isTerminal
          ? t("controlCenter.drawer.followUp")
          : t("controlCenter.drawer.steering")}
      </div>

      {/* Flash message */}
      {flash && (
        <div className="mb-2 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
          {flash}
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            isTerminal
              ? t("controlCenter.drawer.continuePlaceholder")
              : t("controlCenter.drawer.steerPlaceholder")
          }
          disabled={isPending}
          rows={1}
          className={cn(
            "min-h-[36px] max-h-[80px] flex-1 resize-none rounded-xl border border-border bg-muted/50 px-3 py-2 text-sm",
            "placeholder:text-muted-foreground/50",
            "outline-none transition-colors",
            "focus:border-primary/50 focus:ring-1 focus:ring-primary/20",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        />
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!input.trim() || isPending}
          className={cn(
            "flex h-9 shrink-0 items-center gap-1.5 rounded-xl px-3 text-sm font-medium text-white transition-all",
            "bg-primary hover:bg-primary/90",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          {isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <SendHorizontal className="size-3.5" />
          )}
          <span className="hidden sm:inline">
            {isTerminal
              ? t("controlCenter.drawer.newTask")
              : t("controlCenter.drawer.send")}
          </span>
        </button>
      </div>
    </div>
  );
}

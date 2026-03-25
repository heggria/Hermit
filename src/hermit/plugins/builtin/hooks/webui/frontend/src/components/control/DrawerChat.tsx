import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Send } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useSteerTask, useSubmitTask } from "@/api/hooks";
import type { TaskRecord } from "@/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
const MAX_ROWS = 3;
const LINE_HEIGHT_PX = 24;

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
  const [flash, setFlash] = useState<"success" | "error" | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const steerMutation = useSteerTask();
  const submitMutation = useSubmitTask();

  const isTerminal = TERMINAL_STATUSES.has(task.status);
  const isPending = steerMutation.isPending || submitMutation.isPending;

  const showFlash = useCallback((type: "success" | "error", message?: string) => {
    setFlash(type);
    if (message) setErrorMsg(message);
    setTimeout(() => {
      setFlash(null);
      setErrorMsg("");
    }, 3000);
  }, []);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const maxHeight = MAX_ROWS * LINE_HEIGHT_PX;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isPending) return;

    if (isTerminal) {
      // Continue mode: create a new follow-up task
      const description = `[${t("controlCenter.drawer.followUpPrefix")}: ${task.title}] ${trimmed}`;
      submitMutation.mutate(
        { description, policy_profile: task.policy_profile ?? "autonomous" },
        {
          onSuccess: () => {
            setInput("");
            if (textareaRef.current) textareaRef.current.style.height = "auto";
            showFlash("success");
            onNewTask?.();
          },
          onError: (err) => {
            showFlash("error", err instanceof Error ? err.message : String(err));
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
            if (textareaRef.current) textareaRef.current.style.height = "auto";
            showFlash("success");
          },
          onError: (err) => {
            showFlash("error", err instanceof Error ? err.message : String(err));
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

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setInput(e.target.value);
      adjustHeight();
    },
    [adjustHeight],
  );

  const showSuccess = flash === "success";
  const showError = flash === "error";

  return (
    <div className="border-t border-border/50 px-4 pb-3 pt-2.5" data-tour-id="drawer-chat">
      {/* Error feedback */}
      {showError && (
        <div className="mb-2 rounded-lg bg-destructive/10 px-3 py-1.5 text-xs font-medium text-destructive">
          {errorMsg || t("controlCenter.drawer.error")}
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={
              isTerminal
                ? t("controlCenter.drawer.continuePlaceholder")
                : t("controlCenter.drawer.steerPlaceholder")
            }
            disabled={isPending}
            rows={1}
            className="w-full resize-none bg-transparent text-sm leading-6 text-foreground placeholder:text-muted-foreground/50 outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
          {/* Mode indicator */}
          <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground/60">
            <span
              className={cn(
                "size-1.5 rounded-full",
                isTerminal ? "bg-muted-foreground/40" : "bg-primary",
              )}
            />
            {isTerminal
              ? t("controlCenter.drawer.followUp")
              : t("controlCenter.drawer.steering")}
          </div>
        </div>
        <Button
          size="sm"
          disabled={(!input.trim() || isPending) && !showSuccess}
          onClick={handleSubmit}
          className={cn(
            "h-8 shrink-0 px-3 transition-colors",
            showSuccess && "bg-emerald-500 hover:bg-emerald-500 text-white",
          )}
        >
          {isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : showSuccess ? (
            <Check className="size-3.5" />
          ) : (
            <Send className="size-3.5" />
          )}
        </Button>
      </div>
    </div>
  );
}

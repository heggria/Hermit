import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench, Loader2, Check, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/hooks/useWebSocket";

interface ToolExecutionProps {
  startMessage: ChatMessage;
  completeMessage?: ChatMessage;
}

export function ToolExecution({
  startMessage,
  completeMessage,
}: ToolExecutionProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const isComplete = completeMessage !== undefined;
  const toolName = startMessage.name ?? "unknown";
  const hasContent =
    (startMessage.inputs && Object.keys(startMessage.inputs).length > 0) ||
    (isComplete && completeMessage.result);

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] w-full">
        <div
          className={cn(
            "rounded-xl border-l-2 bg-accent/50 overflow-hidden transition-all",
            isComplete ? "border-l-primary/60" : "border-l-muted-foreground/30",
          )}
        >
          {/* Header */}
          <button
            onClick={() => hasContent && setExpanded((prev) => !prev)}
            disabled={!hasContent}
            className={cn(
              "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors",
              hasContent && "hover:bg-accent/80 cursor-pointer",
            )}
          >
            <Wrench className="size-3.5 shrink-0 text-primary/70" />
            <span className="flex-1 text-xs font-medium text-foreground/80 truncate">
              {toolName}
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              {isComplete ? (
                <>
                  <Check className="size-3 text-green-600 dark:text-green-400" />
                  {t("chat.toolCompleted")}
                </>
              ) : (
                <>
                  <Loader2 className="size-3 animate-spin" />
                  {t("chat.toolRunning")}
                </>
              )}
            </span>
            {hasContent && (
              <span className="text-muted-foreground/50">
                {expanded ? (
                  <ChevronDown className="size-3" />
                ) : (
                  <ChevronRight className="size-3" />
                )}
              </span>
            )}
          </button>

          {/* Collapsible content */}
          {expanded && (
            <div className="border-t border-border/30 px-3 py-2 space-y-2">
              {startMessage.inputs &&
                Object.keys(startMessage.inputs).length > 0 && (
                  <div>
                    <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">
                      {t("chat.inputs")}
                    </p>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-muted/50 p-2 text-[11px] text-foreground/80 whitespace-pre-wrap break-words">
                      {JSON.stringify(startMessage.inputs, null, 2)}
                    </pre>
                  </div>
                )}
              {isComplete && completeMessage.result && (
                <div>
                  <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">
                    {t("chat.result")}
                  </p>
                  <pre className="max-h-48 overflow-auto rounded-lg bg-muted/50 p-2 text-[11px] text-foreground/80 whitespace-pre-wrap break-words">
                    {completeMessage.result}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

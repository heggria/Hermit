import { useTranslation } from "react-i18next";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { StepRecord } from "@/types";

// ---------------------------------------------------------------------------
// Compact dot config — 6px colored circles, no text
// ---------------------------------------------------------------------------

const DOT_CONFIG: Record<
  string,
  { color: string; animate?: boolean }
> = {
  completed: {
    color: "bg-emerald-500 dark:bg-emerald-400",
  },
  running: {
    color: "bg-primary",
    animate: true,
  },
  blocked: {
    color: "bg-amber-500 dark:bg-amber-400",
  },
  pending: {
    color: "bg-stone-300 dark:bg-stone-600",
  },
  failed: {
    color: "bg-rose-500 dark:bg-rose-400",
  },
};

function getDotConfig(status: string) {
  return (
    DOT_CONFIG[status] ?? {
      color: "bg-stone-300 dark:bg-stone-600",
    }
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface StepPillsProps {
  readonly steps: StepRecord[];
}

const MAX_VISIBLE = 10;

export function StepPills({ steps }: StepPillsProps) {
  const { t } = useTranslation();

  if (steps.length === 0) {
    return null;
  }

  const visible =
    steps.length > MAX_VISIBLE + 1 ? steps.slice(0, MAX_VISIBLE) : steps;
  const remaining =
    steps.length > MAX_VISIBLE + 1 ? steps.length - MAX_VISIBLE : 0;

  return (
    <TooltipProvider>
      <div className="flex items-center gap-0.5">
        {visible.map((step) => {
          const config = getDotConfig(step.status);
          const label = step.title ?? step.kind;

          return (
            <Tooltip key={step.step_id}>
              <TooltipTrigger asChild>
                <span
                  className={cn(
                    "inline-flex size-1.5 rounded-full transition-colors",
                    config.color,
                    config.animate && "animate-pulse",
                  )}
                />
              </TooltipTrigger>
              <TooltipContent>
                <span className="text-xs">
                  {label} ({t(`common.status.${step.status}`, step.status)})
                </span>
              </TooltipContent>
            </Tooltip>
          );
        })}
        {remaining > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="ml-0.5 text-[10px] tabular-nums text-muted-foreground">
                +{remaining}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <span className="text-xs">
                {t("control.taskCard.moreSteps", { count: remaining })}
              </span>
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </TooltipProvider>
  );
}

import { useTranslation } from "react-i18next";
import {
  CheckCircle,
  Circle,
  Clock,
  GitBranch,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getStatusStyle } from "@/lib/status-styles";
import type { StepRecord } from "@/types";

const STEP_ICONS: Record<string, typeof Circle> = {
  completed: CheckCircle,
  failed: XCircle,
  blocked: Clock,
};

function getStepConfig(status: string) {
  const style = getStatusStyle(status);
  return {
    icon: STEP_ICONS[status] ?? Circle,
    color: style.text,
    bgColor: style.bg,
    textColor: style.text,
    pulses: style.pulse ?? false,
  };
}

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

interface StepTimelineProps {
  readonly steps: StepRecord[];
}

export function StepTimeline({ steps }: StepTimelineProps) {
  const { t } = useTranslation();

  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <Circle className="mb-3 size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {t("taskDetail.steps.noSteps")}
        </p>
      </div>
    );
  }

  return (
    <div className="relative space-y-0 pl-1">
      {steps.map((step, index) => {
        const config = getStepConfig(step.status);
        const Icon = config.icon;
        const duration = formatDuration(step.started_at, step.finished_at);
        const isLast = index === steps.length - 1;

        return (
          <div key={step.step_id} className="relative flex gap-5 pb-8">
            {/* Vertical connecting line */}
            {!isLast && (
              <div className="absolute left-[13px] top-7 h-[calc(100%-16px)] w-px bg-gradient-to-b from-border to-border/40" />
            )}

            {/* Step indicator */}
            <div className="relative z-10 flex-shrink-0 pt-0.5">
              <div
                className={cn(
                  "flex size-7 items-center justify-center rounded-full",
                  config.pulses && "animate-pulse",
                )}
              >
                <Icon className={cn("size-5", config.color)} />
              </div>
            </div>

            {/* Step content */}
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-foreground">
                  {step.title ?? step.kind}
                </span>
                <span
                  className={cn(
                    "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
                    config.bgColor,
                    config.textColor,
                  )}
                >
                  {step.status}
                </span>
                {step.attempt > 1 && (
                  <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                    {t("taskDetail.steps.attempt", { number: step.attempt })}
                  </span>
                )}
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-muted-foreground/60">
                <span className="font-mono">{step.kind}</span>
                {duration && (
                  <span className="flex items-center gap-1">
                    <Clock className="size-3" />
                    {duration}
                  </span>
                )}
              </div>

              {step.depends_on.length > 0 && (
                <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground/60">
                  <GitBranch className="size-3" />
                  <span>
                    {t("taskDetail.steps.dependsOn")}{" "}
                    {step.depends_on.map((dep) => (
                      <code
                        key={dep}
                        className="mr-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                      >
                        {dep.slice(0, 8)}
                      </code>
                    ))}
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

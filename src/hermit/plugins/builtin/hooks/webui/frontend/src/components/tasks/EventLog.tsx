import { useTranslation } from "react-i18next";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatTimeAgo } from "@/lib/format";
import { Calendar } from "lucide-react";

interface EventLogProps {
  readonly events: Record<string, unknown>[];
}

const EVENT_TYPE_STYLES: Record<string, { bg: string; text: string }> = {
  task_created: { bg: "bg-blue-50 dark:bg-blue-950/40", text: "text-blue-600 dark:text-blue-400" },
  task_started: { bg: "bg-blue-50 dark:bg-blue-950/40", text: "text-blue-600 dark:text-blue-400" },
  task_completed: { bg: "bg-emerald-50 dark:bg-emerald-950/40", text: "text-emerald-600 dark:text-emerald-400" },
  task_failed: { bg: "bg-red-50 dark:bg-red-950/40", text: "text-red-600 dark:text-red-400" },
  step_started: { bg: "bg-blue-50 dark:bg-blue-950/40", text: "text-blue-600 dark:text-blue-400" },
  step_completed: { bg: "bg-emerald-50 dark:bg-emerald-950/40", text: "text-emerald-600 dark:text-emerald-400" },
  step_failed: { bg: "bg-red-50 dark:bg-red-950/40", text: "text-red-600 dark:text-red-400" },
  approval_requested: { bg: "bg-amber-50 dark:bg-amber-950/40", text: "text-amber-600 dark:text-amber-400" },
  approval_granted: { bg: "bg-emerald-50 dark:bg-emerald-950/40", text: "text-emerald-600 dark:text-emerald-400" },
  approval_denied: { bg: "bg-red-50 dark:bg-red-950/40", text: "text-red-600 dark:text-red-400" },
  tool_executed: { bg: "bg-violet-50 dark:bg-violet-950/40", text: "text-violet-600 dark:text-violet-400" },
  receipt_issued: { bg: "bg-indigo-50 dark:bg-indigo-950/40", text: "text-indigo-600 dark:text-indigo-400" },
};

function getEventStyle(eventType: string) {
  return (
    EVENT_TYPE_STYLES[eventType] ?? {
      bg: "bg-muted",
      text: "text-muted-foreground",
    }
  );
}

export function EventLog({ events }: EventLogProps) {
  const { t } = useTranslation();

  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <Calendar className="mb-3 size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {t("taskDetail.events.noEvents")}
        </p>
      </div>
    );
  }

  const sorted = [...events].sort((a, b) => {
    const tA = (a.created_at as number) ?? 0;
    const tB = (b.created_at as number) ?? 0;
    return tB - tA;
  });

  return (
    <ScrollArea className="h-[500px] rounded-2xl">
      <div className="space-y-1 pr-4">
        {sorted.map((event, index) => {
          const eventType = (event.event_type as string) ?? "unknown";
          const entityType = (event.entity_type as string) ?? "";
          const actor = (event.actor as string) ?? "";
          const createdAt = event.created_at as number | null;
          const style = getEventStyle(eventType);

          return (
            <div
              key={`${eventType}-${index}`}
              className={`flex items-start gap-4 rounded-xl px-4 py-3 transition-colors ${
                index % 2 === 0 ? "bg-card" : "bg-background"
              } hover:bg-accent/50`}
            >
              {/* Timestamp */}
              <div className="w-16 flex-shrink-0 pt-0.5">
                {createdAt ? (
                  <span className="text-xs text-muted-foreground/60">
                    {formatTimeAgo(createdAt)}
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground/40">--</span>
                )}
              </div>

              {/* Event info */}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${style.bg} ${style.text}`}
                  >
                    {eventType}
                  </span>
                  {entityType && (
                    <span className="text-xs text-muted-foreground/60">
                      {entityType}
                    </span>
                  )}
                </div>
                {actor && (
                  <p className="mt-0.5 text-xs text-muted-foreground/60">
                    {t("taskDetail.events.by", { actor })}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}

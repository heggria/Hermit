import { useTranslation } from "react-i18next";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatTimeAgo } from "@/lib/format";
import { getEventTypeStyle } from "@/lib/status-styles";
import { Calendar } from "lucide-react";

interface EventLogProps {
  readonly events: Record<string, unknown>[];
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
          const style = getEventTypeStyle(eventType);

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

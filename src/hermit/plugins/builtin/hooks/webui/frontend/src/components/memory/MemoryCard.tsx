import { FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MemoryRecord } from "@/types";

interface MemoryCardProps {
  memory: MemoryRecord;
}

function formatRelativeTime(
  timestamp: number | null,
  t: (key: string, opts?: Record<string, unknown>) => string
): string {
  if (timestamp === null) return t("common.unknown");

  const now = Date.now();
  const ms = timestamp < 1e12 ? timestamp * 1000 : timestamp;
  const diff = now - ms;

  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return t("common.justNow");

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("common.minutesAgo", { count: minutes });

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("common.hoursAgo", { count: hours });

  const days = Math.floor(hours / 24);
  if (days < 30) return t("common.daysAgo", { count: days });

  const months = Math.floor(days / 30);
  return t("common.monthsAgo", { count: months });
}

function confidenceGradient(confidence: number): string {
  if (confidence < 0.3) return "from-stone-400 to-stone-500 dark:from-stone-500 dark:to-stone-600";
  if (confidence < 0.7) return "from-amber-400 to-amber-500 dark:from-amber-500 dark:to-amber-600";
  return "from-primary to-primary/80";
}

export function MemoryCard({ memory }: MemoryCardProps) {
  const { t } = useTranslation();
  const confidencePct = Math.round(memory.confidence * 100);

  return (
    <div className="group rounded-2xl bg-card border border-border p-5 shadow-sm transition-all duration-200 hover:-translate-y-[1px] hover:shadow-md">
      {/* Claim text */}
      <p className="text-sm leading-relaxed text-foreground">
        {memory.claim_text}
      </p>

      {/* Bottom row */}
      <div className="mt-4 space-y-3">
        {/* Confidence bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{t("memory.confidence")}</span>
            <span className="tabular-nums font-medium text-foreground/80">
              {confidencePct}%
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full bg-gradient-to-r ${confidenceGradient(memory.confidence)} transition-all duration-500`}
              style={{ width: `${confidencePct}%` }}
            />
          </div>
        </div>

        {/* Category + evidence */}
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            {memory.category}
          </span>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <div className="flex items-center gap-1">
              <FileText className="size-3" />
              <span>
                {t("memory.evidenceCount", {
                  count: memory.evidence_refs.length,
                })}
              </span>
            </div>
            <span>{formatRelativeTime(memory.created_at, t)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

import { FileText, Sparkles } from "lucide-react";
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
  if (seconds < 60) return t("common.time.justNow");

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("common.time.minutesAgo", { count: minutes });

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("common.time.hoursAgo", { count: hours });

  const days = Math.floor(hours / 24);
  if (days < 30) return t("common.time.daysAgo", { count: days });

  const months = Math.floor(days / 30);
  return t("common.time.monthsAgo", { count: months });
}

/** Accent color for the left border, based on confidence tier. */
function confidenceAccent(confidence: number): string {
  if (confidence >= 0.8) return "border-l-emerald-500 dark:border-l-emerald-400";
  if (confidence >= 0.5) return "border-l-amber-500 dark:border-l-amber-400";
  return "border-l-stone-400 dark:border-l-stone-500";
}

/** Progress bar gradient. */
function confidenceBarColor(confidence: number): string {
  if (confidence >= 0.8) return "bg-emerald-500 dark:bg-emerald-400";
  if (confidence >= 0.5) return "bg-amber-500 dark:bg-amber-400";
  return "bg-stone-400 dark:bg-stone-500";
}

/** Category → accent pill colors. */
const CATEGORY_STYLES: Record<string, string> = {
  contract_template:
    "bg-violet-50 text-violet-600 dark:bg-violet-900/40 dark:text-violet-400",
  execution_pattern:
    "bg-blue-50 text-blue-600 dark:bg-blue-900/40 dark:text-blue-400",
  policy_learning:
    "bg-amber-50 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400",
  risk_assessment:
    "bg-red-50 text-red-600 dark:bg-red-900/40 dark:text-red-400",
};

const DEFAULT_CATEGORY_STYLE =
  "bg-stone-100 text-stone-500 dark:bg-stone-800/50 dark:text-stone-400";

/** Importance dots (1-5 scale mapped to 3 dots). */
function ImportanceDots({ value }: { value: number }) {
  const level = value >= 0.8 ? 3 : value >= 0.4 ? 2 : 1;
  return (
    <div className="flex items-center gap-0.5" title={`${Math.round(value * 100)}%`}>
      {[1, 2, 3].map((i) => (
        <span
          key={i}
          className={`inline-block size-1.5 rounded-full transition-colors ${
            i <= level
              ? "bg-primary/80 dark:bg-primary/70"
              : "bg-muted-foreground/15"
          }`}
        />
      ))}
    </div>
  );
}

export function MemoryCard({ memory }: MemoryCardProps) {
  const { t } = useTranslation();
  const confidencePct = Math.round(memory.confidence * 100);
  const categoryStyle =
    CATEGORY_STYLES[memory.category] ?? DEFAULT_CATEGORY_STYLE;

  return (
    <div
      className={`group relative overflow-hidden rounded-xl border-l-[3px] bg-card border border-border shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md ${confidenceAccent(memory.confidence)}`}
    >
      <div className="p-4 space-y-3">
        {/* Header: category + importance */}
        <div className="flex items-center justify-between gap-2">
          <span
            className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${categoryStyle}`}
          >
            <Sparkles className="size-3 opacity-60" />
            {t(`memory.category.${memory.category}`, memory.category)}
          </span>
          <ImportanceDots value={memory.importance} />
        </div>

        {/* Claim text */}
        <p className="text-[13px] leading-relaxed text-foreground/90 line-clamp-4">
          {memory.claim_text}
        </p>

        {/* Confidence bar */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-muted-foreground/70">
              {t("memory.confidence")}
            </span>
            <span className="text-[11px] tabular-nums font-semibold text-foreground/70">
              {confidencePct}%
            </span>
          </div>
          <div className="h-1 w-full overflow-hidden rounded-full bg-muted/60">
            <div
              className={`h-full rounded-full ${confidenceBarColor(memory.confidence)} transition-all duration-700 ease-out`}
              style={{ width: `${confidencePct}%` }}
            />
          </div>
        </div>

        {/* Footer: evidence + time */}
        <div className="flex items-center justify-between pt-0.5">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/60">
            <FileText className="size-3" />
            <span>
              {t("memory.card.evidence", {
                count: memory.evidence_refs.length,
              })}
            </span>
          </div>
          <span className="text-[11px] text-muted-foreground/50 tabular-nums">
            {formatRelativeTime(memory.created_at, t)}
          </span>
        </div>
      </div>

      {/* Subtle shimmer on hover */}
      <div className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100 bg-gradient-to-br from-primary/[0.02] to-transparent" />
    </div>
  );
}

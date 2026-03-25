import { Zap, Ban, Clock, Target } from "lucide-react";
import { useTranslation } from "react-i18next";
import { getRiskStyle } from "@/lib/status-styles";
import type { EvidenceSignal } from "@/types";

interface SignalCardProps {
  signal: EvidenceSignal;
  onAct: (signalId: string) => void;
  onSuppress: (signalId: string) => void;
  isActing: boolean;
}

/** Risk level → left accent border. */
function riskAccent(risk: string): string {
  switch (risk) {
    case "critical":
      return "border-l-red-500 dark:border-l-red-400";
    case "high":
      return "border-l-orange-500 dark:border-l-orange-400";
    case "medium":
      return "border-l-amber-500 dark:border-l-amber-400";
    default:
      return "border-l-emerald-500 dark:border-l-emerald-400";
  }
}

/** Source kind → icon accent colors. */
const SOURCE_STYLES: Record<string, string> = {
  test_failure:
    "bg-red-50 text-red-600 dark:bg-red-900/40 dark:text-red-400",
  security_vuln:
    "bg-orange-50 text-orange-600 dark:bg-orange-900/40 dark:text-orange-400",
  lint_violation:
    "bg-amber-50 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400",
  coverage_drop:
    "bg-blue-50 text-blue-600 dark:bg-blue-900/40 dark:text-blue-400",
  todo_scan:
    "bg-violet-50 text-violet-600 dark:bg-violet-900/40 dark:text-violet-400",
};

const DEFAULT_SOURCE_STYLE =
  "bg-stone-100 text-stone-500 dark:bg-stone-800/50 dark:text-stone-400";

/** Disposition badge styles. */
function dispositionStyle(d: string): string {
  switch (d) {
    case "pending":
      return "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
    case "acted":
      return "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
    case "suppressed":
    case "dismissed":
      return "bg-stone-100 text-stone-500 dark:bg-stone-800/50 dark:text-stone-400";
    default:
      return "bg-muted text-muted-foreground";
  }
}

function formatRelativeTime(
  timestamp: number,
  t: (key: string, opts?: Record<string, unknown>) => string
): string {
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

export function SignalCard({
  signal,
  onAct,
  onSuppress,
  isActing,
}: SignalCardProps) {
  const { t } = useTranslation();
  const confidencePct = Math.round(signal.confidence * 100);
  const isPending = signal.disposition === "pending";
  const riskStyle = getRiskStyle(signal.risk_level);
  const sourceStyle = SOURCE_STYLES[signal.source_kind] ?? DEFAULT_SOURCE_STYLE;

  const isExpired =
    signal.expires_at !== null &&
    Date.now() > (signal.expires_at < 1e12 ? signal.expires_at * 1000 : signal.expires_at);

  return (
    <div
      className={`group relative overflow-hidden rounded-xl border-l-[3px] bg-card border border-border shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md ${riskAccent(signal.risk_level)} ${isExpired ? "opacity-60" : ""}`}
    >
      <div className="p-4 space-y-3">
        {/* Header: source pill + risk + confidence */}
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${sourceStyle}`}
          >
            {t(`signals.source.${signal.source_kind}`, signal.source_kind)}
          </span>
          <span
            className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${riskStyle.bg} ${riskStyle.text}`}
          >
            {signal.risk_level}
          </span>
          {isExpired && (
            <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              <Clock className="size-3" />
              {t("signals.card.expired")}
            </span>
          )}
          <span className="ml-auto text-[11px] tabular-nums font-semibold text-foreground/60">
            {confidencePct}%
          </span>
        </div>

        {/* Summary */}
        <p className="text-[13px] leading-relaxed text-foreground/90 line-clamp-3">
          {signal.summary}
        </p>

        {/* Suggested goal */}
        {signal.suggested_goal && (
          <div className="flex items-start gap-2 rounded-lg bg-secondary/60 px-3 py-2">
            <Target className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/50" />
            <p className="text-[12px] leading-relaxed text-muted-foreground line-clamp-2">
              {signal.suggested_goal}
            </p>
          </div>
        )}

        {/* Footer: time + actions */}
        <div className="flex items-center justify-between pt-0.5">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium ${dispositionStyle(signal.disposition)}`}
            >
              {signal.disposition}
            </span>
            <span className="text-[11px] text-muted-foreground/50 tabular-nums">
              {formatRelativeTime(signal.created_at, t)}
            </span>
          </div>

          {isPending && !isExpired && (
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => onAct(signal.signal_id)}
                disabled={isActing}
                className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1 text-[11px] font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                <Zap className="size-3" />
                {t("signals.act")}
              </button>
              <button
                onClick={() => onSuppress(signal.signal_id)}
                disabled={isActing}
                className="inline-flex items-center gap-1 rounded-lg border border-border bg-card px-3 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
              >
                <Ban className="size-3" />
                {t("signals.suppress")}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Hover shimmer */}
      <div className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100 bg-gradient-to-br from-primary/[0.02] to-transparent" />
    </div>
  );
}

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useGovernanceMetrics } from "@/api/hooks";
import { ToolUsageChart } from "@/components/metrics/ToolUsageChart";
import { ActionClassChart } from "@/components/metrics/ActionClassChart";
import { RiskTable } from "@/components/metrics/RiskTable";

const TIME_WINDOWS = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
] as const;

function useWindowDescription(hours: number): string {
  const { t } = useTranslation();
  if (hours < 24) return t("metrics.lastHours", { count: hours });
  const days = hours / 24;
  return t("metrics.lastDays", { count: days });
}

function approvalRateColor(rate: number): string {
  const pct = rate * 100;
  if (pct > 80) return "text-emerald-600 dark:text-emerald-400";
  if (pct > 50) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function rollbackRateColor(rate: number): string {
  const pct = rate * 100;
  if (pct > 10) return "text-red-600 dark:text-red-400";
  if (pct > 5) return "text-amber-600 dark:text-amber-400";
  return "text-emerald-600 dark:text-emerald-400";
}

export default function Metrics() {
  const { t } = useTranslation();
  const [hours, setHours] = useState(24);
  const { data, isLoading } = useGovernanceMetrics(hours);

  const throughput = data?.task_throughput ?? 0;
  const approvalRate = data?.approval_rate ?? 0;
  const rollbackRate = data?.rollback_rate ?? 0;
  const avgLatency = data?.avg_approval_latency ?? 0;
  const toolUsage = data?.tool_usage_counts ?? {};
  const actionDist = data?.action_class_distribution ?? {};
  const riskEntries = data?.risk_entries ?? [];
  const windowDesc = useWindowDescription(hours);

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      {/* Header with time window selector */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {t("metrics.title")}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("metrics.description", { window: windowDesc })}
          </p>
        </div>
        <div className="flex gap-1 rounded-full border border-border bg-card p-1">
          {TIME_WINDOWS.map((tw) => (
            <button
              key={tw.hours}
              onClick={() => setHours(tw.hours)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
                hours === tw.hours
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {tw.label}
            </button>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="rounded-2xl bg-card border border-border p-5 shadow-sm"
            >
              <div className="h-3 w-20 animate-pulse rounded bg-muted" />
              <div className="mt-3 h-8 w-16 animate-pulse rounded bg-muted" />
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl bg-card border border-border p-5 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              {t("metrics.taskThroughput")}
            </p>
            <p className="mt-2 text-3xl font-bold tabular-nums text-foreground">
              {throughput.toFixed(1)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("metrics.taskThroughputUnit", { window: windowDesc })}
            </p>
          </div>

          <div className="rounded-2xl bg-card border border-border p-5 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              {t("metrics.approvalRate")}
            </p>
            <p
              className={`mt-2 text-3xl font-bold tabular-nums ${approvalRateColor(approvalRate)}`}
            >
              {(approvalRate * 100).toFixed(1)}%
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("metrics.approvalRateDesc")}
            </p>
          </div>

          <div className="rounded-2xl bg-card border border-border p-5 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              {t("metrics.rollbackRate")}
            </p>
            <p
              className={`mt-2 text-3xl font-bold tabular-nums ${rollbackRateColor(rollbackRate)}`}
            >
              {(rollbackRate * 100).toFixed(1)}%
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("metrics.rollbackRateDesc")}
            </p>
          </div>

          <div className="rounded-2xl bg-card border border-border p-5 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              {t("metrics.avgLatency")}
            </p>
            <p className="mt-2 text-3xl font-bold tabular-nums text-foreground">
              {avgLatency.toFixed(1)}s
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("metrics.avgLatencyDesc")}
            </p>
          </div>
        </div>
      )}

      {/* Charts row */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ToolUsageChart toolUsageCounts={toolUsage} />
        <ActionClassChart distribution={actionDist} />
      </div>

      {/* Risk entries table */}
      <RiskTable entries={riskEntries} />
    </div>
  );
}

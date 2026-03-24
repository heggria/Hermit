import {
  Gauge,
  RotateCcw,
  Clock,
  TrendingUp,
} from "lucide-react";
import { useGovernanceMetrics } from "@/api/hooks";
import { useTranslation } from "react-i18next";

interface MetricCardProps {
  readonly label: string;
  readonly value: string;
  readonly unit: string;
  readonly icon: React.ReactNode;
  readonly colorClass?: string;
  readonly delay: string;
}

function MetricCard({ label, value, unit, icon, colorClass, delay }: MetricCardProps) {
  return (
    <div
      className="animate-slide-up rounded-2xl bg-card p-5 shadow-sm ring-1 ring-border/50"
      style={{ animationDelay: delay }}
    >
      <div className="flex items-center gap-2 text-muted-foreground">
        <span className="opacity-60">{icon}</span>
        <p className="text-xs font-medium uppercase tracking-wider">{label}</p>
      </div>
      <p className={`mt-2 text-2xl font-bold tabular-nums ${colorClass ?? "text-foreground"}`}>
        {value}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">{unit}</p>
    </div>
  );
}

export function MetricsSummary() {
  const { data, isLoading } = useGovernanceMetrics(24);
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="animate-fade-in space-y-4">
        <h3 className="text-base font-semibold text-foreground">
          {t("dashboard.metricsSummary.title")}
        </h3>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-2xl bg-card p-5 shadow-sm ring-1 ring-border/50">
              <div className="h-4 w-20 animate-pulse rounded bg-muted" />
              <div className="mt-3 h-7 w-16 animate-pulse rounded bg-muted" />
              <div className="mt-2 h-3 w-12 animate-pulse rounded bg-muted" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const metrics = data;
  const approvalRate = metrics?.approval_rate ?? 0;
  const rollbackRate = metrics?.rollback_rate ?? 0;
  const avgLatency = metrics?.avg_approval_latency ?? 0;
  const throughput = metrics?.task_throughput ?? 0;

  const approvalPct = approvalRate * 100;
  const approvalColor =
    approvalPct >= 90
      ? "text-emerald-600 dark:text-emerald-400"
      : approvalPct >= 70
        ? "text-amber-600 dark:text-amber-400"
        : "text-rose-600 dark:text-rose-400";

  return (
    <div className="animate-fade-in space-y-4" style={{ animationDelay: "0.3s" }}>
      <h3 className="text-base font-semibold text-foreground">
        {t("dashboard.governanceMetrics")}
      </h3>
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label={t("dashboard.metricsSummary.approvalRate")}
          value={`${approvalPct.toFixed(1)}%`}
          unit="of governed actions"
          icon={<Gauge className="size-4" />}
          colorClass={approvalColor}
          delay="0.35s"
        />
        <MetricCard
          label={t("dashboard.metricsSummary.rollbackRate")}
          value={`${(rollbackRate * 100).toFixed(1)}%`}
          unit="of executions"
          icon={<RotateCcw className="size-4" />}
          delay="0.4s"
        />
        <MetricCard
          label={t("dashboard.metricsSummary.avgLatency")}
          value={`${avgLatency.toFixed(1)}s`}
          unit="median wait"
          icon={<Clock className="size-4" />}
          delay="0.45s"
        />
        <MetricCard
          label={t("dashboard.metricsSummary.throughput")}
          value={`${throughput.toFixed(1)}`}
          unit="tasks / hour"
          icon={<TrendingUp className="size-4" />}
          delay="0.5s"
        />
      </div>
    </div>
  );
}

import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { useMetricsSummary } from "@/api/hooks";
import { useTranslation } from "react-i18next";

interface StatCardProps {
  readonly title: string;
  readonly value: number;
  readonly icon: React.ReactNode;
  readonly bgClass: string;
  readonly iconColorClass: string;
  readonly delay: string;
}

function StatCard({ title, value, icon, bgClass, iconColorClass, delay }: StatCardProps) {
  return (
    <div
      className={`animate-slide-up relative overflow-hidden rounded-2xl ${bgClass} p-6 shadow-sm`}
      style={{ animationDelay: delay }}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-3xl font-bold tabular-nums text-foreground">
            {value}
          </p>
          <p className="mt-1 text-sm font-medium text-muted-foreground">
            {title}
          </p>
        </div>
        <span className={`${iconColorClass} opacity-60`}>
          {icon}
        </span>
      </div>
    </div>
  );
}

export function StatsCards() {
  const { data, isLoading } = useMetricsSummary();
  const { t } = useTranslation();

  const byStatus = data?.by_status ?? {};
  const running = byStatus["running"] ?? 0;
  const blocked = byStatus["blocked"] ?? 0;
  const completed24h = byStatus["completed"] ?? 0;
  const failed24h = byStatus["failed"] ?? 0;

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="rounded-2xl bg-card p-6 shadow-sm"
          >
            <div className="h-8 w-16 animate-pulse rounded-lg bg-muted" />
            <div className="mt-2 h-4 w-24 animate-pulse rounded bg-muted" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        title={t("dashboard.statsCards.running")}
        value={running}
        icon={<Activity className="size-5" />}
        bgClass="bg-blue-50 dark:bg-blue-950/30"
        iconColorClass="text-blue-500"
        delay="0s"
      />
      <StatCard
        title={t("dashboard.statsCards.blocked")}
        value={blocked}
        icon={<AlertTriangle className="size-5" />}
        bgClass="bg-amber-50 dark:bg-amber-950/30"
        iconColorClass="text-amber-500"
        delay="0.1s"
      />
      <StatCard
        title={t("dashboard.statsCards.completed")}
        value={completed24h}
        icon={<CheckCircle2 className="size-5" />}
        bgClass="bg-emerald-50 dark:bg-emerald-950/30"
        iconColorClass="text-emerald-500"
        delay="0.2s"
      />
      <StatCard
        title={t("dashboard.statsCards.failed")}
        value={failed24h}
        icon={<XCircle className="size-5" />}
        bgClass="bg-rose-50 dark:bg-rose-950/30"
        iconColorClass="text-rose-500"
        delay="0.3s"
      />
    </div>
  );
}

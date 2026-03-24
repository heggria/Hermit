import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { StatsCards } from "@/components/dashboard/StatsCards";
import { RecentTasks } from "@/components/dashboard/RecentTasks";
import { PendingApprovals } from "@/components/dashboard/PendingApprovals";
import { MetricsSummary } from "@/components/dashboard/MetricsSummary";
import { createEventSource } from "@/lib/sse";

export default function Dashboard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  useEffect(() => {
    const cleanup = createEventSource("/api/events", {
      task_update: () => {
        queryClient.invalidateQueries({ queryKey: ["tasks"] });
        queryClient.invalidateQueries({ queryKey: ["metrics", "summary"] });
      },
      approval_update: () => {
        queryClient.invalidateQueries({ queryKey: ["approvals"] });
        queryClient.invalidateQueries({ queryKey: ["tasks"] });
      },
      metrics_update: () => {
        queryClient.invalidateQueries({ queryKey: ["metrics"] });
      },
    });

    return cleanup;
  }, [queryClient]);

  return (
    <div className="space-y-8">
      <div className="animate-fade-in">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          {t("dashboard.title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("dashboard.subtitle")}
        </p>
      </div>

      <StatsCards />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <RecentTasks />
        <PendingApprovals />
      </div>

      <MetricsSummary />
    </div>
  );
}

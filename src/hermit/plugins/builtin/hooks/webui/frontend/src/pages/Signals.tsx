import { useState, useMemo, useCallback } from "react";
import {
  Radio,
  Search,
  Activity,
  AlertTriangle,
  Clock,
  Inbox,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { SignalCard } from "@/components/signals/SignalCard";
import { useSignals, useSignalAction, useSignalStats } from "@/api/hooks";
import { DataContainer } from "@/components/ui/DataContainer";
import { EmptyState } from "@/components/layout/EmptyState";
import { CardGridSkeleton } from "@/components/ui/skeletons";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { useFilteredData } from "@/hooks/useFilteredData";
import type { EvidenceSignal } from "@/types";

const DISPOSITION_FILTERS = ["pending", "acted", "dismissed", "all"] as const;

/** Stats KPI card. */
function StatCard({
  icon,
  iconBg,
  label,
  value,
  desc,
}: {
  icon: React.ReactNode;
  iconBg: string;
  label: string;
  value: string | number;
  desc: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-4 shadow-sm">
      <div
        className={`flex size-10 shrink-0 items-center justify-center rounded-lg ${iconBg}`}
      >
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="text-xl font-bold tabular-nums text-foreground leading-tight">
          {value}
        </p>
        <p className="text-[11px] text-muted-foreground/60 truncate">{desc}</p>
      </div>
    </div>
  );
}

/** Loading skeleton for stats row. */
function StatsRowSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-xl border border-border bg-card p-4"
        >
          <div className="size-10 shrink-0 animate-pulse rounded-lg bg-muted" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3 w-16 animate-pulse rounded bg-muted" />
            <div className="h-5 w-10 animate-pulse rounded bg-muted" />
            <div className="h-2.5 w-20 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Signals() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const { data, isLoading } = useSignals();
  const { data: stats, isLoading: statsLoading } = useSignalStats();
  const signalAction = useSignalAction();

  const signals = data?.signals ?? [];

  const getStatus = useCallback((s: EvidenceSignal) => s.disposition, []);
  const labelFn = useCallback(
    (key: string) => {
      switch (key) {
        case "all":
          return t("signals.filterAll");
        case "pending":
          return t("signals.filterPending");
        case "acted":
          return t("signals.filterActed");
        case "dismissed":
          return t("signals.filterDismissed");
        default:
          return key;
      }
    },
    [t],
  );

  const { filtered: dispositionFiltered, activeTab, setActiveTab, filterTabs } =
    useFilteredData(signals, DISPOSITION_FILTERS, getStatus, labelFn);

  // Apply text search
  const filtered = useMemo(() => {
    if (!search.trim()) return dispositionFiltered;
    const query = search.toLowerCase();
    return dispositionFiltered.filter(
      (s) =>
        s.summary.toLowerCase().includes(query) ||
        s.source_kind.toLowerCase().includes(query) ||
        s.suggested_goal.toLowerCase().includes(query),
    );
  }, [dispositionFiltered, search]);

  const handleAct = (signalId: string) => {
    signalAction.mutate({ signalId, action: "act" });
  };

  const handleSuppress = (signalId: string) => {
    signalAction.mutate({ signalId, action: "suppress" });
  };

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {t("signals.title")}
          </h1>
          {stats && stats.pending_count > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-0.5 text-xs font-semibold text-primary-foreground">
              {t("signals.actionable", { count: stats.pending_count })}
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("signals.subtitle")}
        </p>
      </div>

      {/* Stats overview */}
      {statsLoading ? (
        <StatsRowSkeleton />
      ) : stats ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            icon={
              <Activity className="size-5 text-blue-600 dark:text-blue-400" />
            }
            iconBg="bg-blue-50 dark:bg-blue-900/40"
            label={t("signals.stats.total")}
            value={stats.total}
            desc={t("signals.stats.totalDesc")}
          />
          <StatCard
            icon={
              <Inbox className="size-5 text-amber-600 dark:text-amber-400" />
            }
            iconBg="bg-amber-50 dark:bg-amber-900/40"
            label={t("signals.stats.pending")}
            value={stats.pending_count}
            desc={t("signals.stats.pendingDesc")}
          />
          <StatCard
            icon={
              <AlertTriangle className="size-5 text-red-600 dark:text-red-400" />
            }
            iconBg="bg-red-50 dark:bg-red-900/40"
            label={t("signals.stats.highRisk")}
            value={stats.high_risk_count}
            desc={t("signals.stats.highRiskDesc")}
          />
          <StatCard
            icon={
              <Clock className="size-5 text-emerald-600 dark:text-emerald-400" />
            }
            iconBg="bg-emerald-50 dark:bg-emerald-900/40"
            label={t("signals.stats.recent")}
            value={stats.recent_count}
            desc={t("signals.stats.recentDesc")}
          />
        </div>
      ) : null}

      {/* Search + filters row */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/50" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("signals.loading").replace("...", "")}
            className="w-full rounded-lg border border-border bg-card/80 py-2 pl-10 pr-4 text-sm text-foreground placeholder:text-muted-foreground/50 outline-none transition-all focus:border-primary/50 focus:ring-2 focus:ring-primary/10 focus:bg-card"
          />
        </div>
        <FilterTabs
          tabs={filterTabs}
          activeTab={activeTab}
          onChange={setActiveTab}
        />
      </div>

      {/* Content */}
      <DataContainer
        isLoading={isLoading}
        isEmpty={filtered.length === 0}
        skeleton={
          <CardGridSkeleton
            count={6}
            height="h-44"
            columns="sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
          />
        }
        emptyState={
          <EmptyState
            icon={<Radio className="size-5 text-muted-foreground/60" />}
            title={t("signals.noResults")}
            subtitle={t("signals.noResultsHint")}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((signal) => (
            <SignalCard
              key={signal.signal_id}
              signal={signal}
              onAct={handleAct}
              onSuppress={handleSuppress}
              isActing={signalAction.isPending}
            />
          ))}
        </div>
      </DataContainer>
    </div>
  );
}

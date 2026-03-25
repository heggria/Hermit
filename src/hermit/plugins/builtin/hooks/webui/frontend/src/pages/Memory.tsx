import { useState, useMemo, useCallback } from "react";
import { Search, Brain, TrendingUp, Shield, Clock, Database } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MemoryCard } from "@/components/memory/MemoryCard";
import { useMemories, useMemoryStats } from "@/api/hooks";
import { DataContainer } from "@/components/ui/DataContainer";
import { EmptyState } from "@/components/layout/EmptyState";
import { CardGridSkeleton } from "@/components/ui/skeletons";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { useFilteredData } from "@/hooks/useFilteredData";
import type { MemoryRecord } from "@/types";

const STATUS_FILTERS = ["active", "invalidated", "all"] as const;

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

export default function Memory() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const { data, isLoading } = useMemories();
  const { data: stats, isLoading: statsLoading } = useMemoryStats();

  const memories = data?.memories ?? [];

  const getStatus = useCallback((m: MemoryRecord) => m.status, []);
  const labelFn = useCallback(
    (key: string) => {
      switch (key) {
        case "all":
          return t("memory.filterAll");
        case "active":
          return t("memory.filterActive");
        case "invalidated":
          return t("memory.filterInvalidated");
        default:
          return key;
      }
    },
    [t],
  );

  const { filtered: statusFiltered, activeTab, setActiveTab, filterTabs } =
    useFilteredData(memories, STATUS_FILTERS, getStatus, labelFn);

  // Apply text search on top of status filter
  const filtered = useMemo(() => {
    if (!search.trim()) return statusFiltered;
    const query = search.toLowerCase();
    return statusFiltered.filter(
      (m) =>
        m.claim_text.toLowerCase().includes(query) ||
        m.category.toLowerCase().includes(query),
    );
  }, [statusFiltered, search]);

  const avgConfidencePct = stats ? Math.round(stats.avg_confidence * 100) : 0;

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {t("memory.title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("memory.subtitle")}
        </p>
      </div>

      {/* Stats overview */}
      {statsLoading ? (
        <StatsRowSkeleton />
      ) : stats ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            icon={<Database className="size-5 text-blue-600 dark:text-blue-400" />}
            iconBg="bg-blue-50 dark:bg-blue-900/40"
            label={t("memory.stats.total")}
            value={stats.total}
            desc={t("memory.stats.totalDesc")}
          />
          <StatCard
            icon={<TrendingUp className="size-5 text-emerald-600 dark:text-emerald-400" />}
            iconBg="bg-emerald-50 dark:bg-emerald-900/40"
            label={t("memory.stats.avgConfidence")}
            value={`${avgConfidencePct}%`}
            desc={t("memory.stats.avgConfidenceDesc")}
          />
          <StatCard
            icon={<Shield className="size-5 text-violet-600 dark:text-violet-400" />}
            iconBg="bg-violet-50 dark:bg-violet-900/40"
            label={t("memory.stats.evidenceBacked")}
            value={stats.evidence_backed_count}
            desc={t("memory.stats.evidenceBackedDesc")}
          />
          <StatCard
            icon={<Clock className="size-5 text-amber-600 dark:text-amber-400" />}
            iconBg="bg-amber-50 dark:bg-amber-900/40"
            label={t("memory.stats.recentPromotions")}
            value={stats.recent_promotions}
            desc={t("memory.stats.recentPromotionsDesc")}
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
            placeholder={t("memory.searchPlaceholder")}
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
            height="h-40"
            columns="sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
          />
        }
        emptyState={
          <EmptyState
            icon={<Brain className="size-5 text-muted-foreground/60" />}
            title={t("memory.noResults")}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((memory) => (
            <MemoryCard key={memory.memory_id} memory={memory} />
          ))}
        </div>
      </DataContainer>
    </div>
  );
}

import { useState, useMemo, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  ShieldAlert,
  Search,
  Clock,
  CheckCircle2,
  XCircle,
  Inbox,
} from "lucide-react";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import {
  useApprovals,
  useApprovalStats,
  useApproveMutation,
  useDenyMutation,
} from "@/api/hooks";
import { DataContainer } from "@/components/ui/DataContainer";
import { EmptyState } from "@/components/layout/EmptyState";
import { CardGridSkeleton } from "@/components/ui/skeletons";
import { FilterTabs } from "@/components/ui/FilterTabs";
import { useFilteredData } from "@/hooks/useFilteredData";
import type { ApprovalRecord } from "@/types";

const STATUS_FILTERS = ["pending", "approved", "denied", "all"] as const;

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

export default function Approvals() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [processingAction, setProcessingAction] = useState<
    "approve" | "deny" | null
  >(null);

  const approvalsQuery = useApprovals(undefined, 200);
  const { data: stats, isLoading: statsLoading } = useApprovalStats();
  const approveMutation = useApproveMutation();
  const denyMutation = useDenyMutation();

  const approvals = approvalsQuery.data?.approvals ?? [];

  const getStatus = useCallback((a: ApprovalRecord) => a.status, []);
  const labelFn = useCallback(
    (key: string) => {
      switch (key) {
        case "all":
          return t("approvals.filterAll");
        case "pending":
          return t("approvals.filterPending");
        case "approved":
          return t("approvals.filterApproved");
        case "denied":
          return t("approvals.filterDenied");
        default:
          return key;
      }
    },
    [t],
  );

  const { filtered: statusFiltered, activeTab, setActiveTab, filterTabs } =
    useFilteredData(approvals, STATUS_FILTERS, getStatus, labelFn);

  // Apply text search on top of status filter
  const filtered = useMemo(() => {
    if (!search.trim()) return statusFiltered;
    const query = search.toLowerCase();
    return statusFiltered.filter((a) => {
      const action = a.requested_action;
      const toolName =
        (action.tool_name as string) ?? (action.tool as string) ?? "";
      const toolInput = action.tool_input ?? action.input ?? action.arguments ?? {};
      const inputStr =
        typeof toolInput === "string" ? toolInput : JSON.stringify(toolInput);
      return (
        toolName.toLowerCase().includes(query) ||
        inputStr.toLowerCase().includes(query) ||
        a.task_id.toLowerCase().includes(query)
      );
    });
  }, [statusFiltered, search]);

  async function handleApprove(approvalId: string) {
    setProcessingId(approvalId);
    setProcessingAction("approve");
    try {
      await approveMutation.mutateAsync(approvalId);
    } finally {
      setProcessingId(null);
      setProcessingAction(null);
    }
  }

  async function handleDeny(approvalId: string, reason: string) {
    setProcessingId(approvalId);
    setProcessingAction("deny");
    try {
      await denyMutation.mutateAsync({ approvalId, reason });
    } finally {
      setProcessingId(null);
      setProcessingAction(null);
    }
  }

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {t("approvals.title")}
          </h1>
          {stats && stats.pending > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-0.5 text-xs font-semibold text-primary-foreground">
              {t("approvals.pendingCount", { count: stats.pending })}
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("approvals.subtitle")}
        </p>
      </div>

      {/* Stats overview */}
      {statsLoading ? (
        <StatsRowSkeleton />
      ) : stats ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            icon={<Inbox className="size-5 text-amber-600 dark:text-amber-400" />}
            iconBg="bg-amber-50 dark:bg-amber-900/40"
            label={t("approvals.stats.pending")}
            value={stats.pending}
            desc={t("approvals.stats.pendingDesc")}
          />
          <StatCard
            icon={<CheckCircle2 className="size-5 text-emerald-600 dark:text-emerald-400" />}
            iconBg="bg-emerald-50 dark:bg-emerald-900/40"
            label={t("approvals.stats.approved")}
            value={stats.approved}
            desc={t("approvals.stats.approvedDesc")}
          />
          <StatCard
            icon={<XCircle className="size-5 text-red-600 dark:text-red-400" />}
            iconBg="bg-red-50 dark:bg-red-900/40"
            label={t("approvals.stats.denied")}
            value={stats.denied}
            desc={t("approvals.stats.deniedDesc")}
          />
          <StatCard
            icon={<Clock className="size-5 text-blue-600 dark:text-blue-400" />}
            iconBg="bg-blue-50 dark:bg-blue-900/40"
            label={t("approvals.stats.recent")}
            value={stats.recent_24h}
            desc={t("approvals.stats.recentDesc")}
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
            placeholder={t("approvals.searchPlaceholder")}
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
        isLoading={approvalsQuery.isLoading}
        isEmpty={filtered.length === 0}
        skeleton={
          <CardGridSkeleton
            count={4}
            height="h-44"
            columns="sm:grid-cols-1 lg:grid-cols-2"
          />
        }
        emptyState={
          <EmptyState
            icon={<ShieldAlert className="size-5 text-muted-foreground/60" />}
            title={t("approvals.noResults")}
            subtitle={t("approvals.noResultsHint")}
            layout="horizontal"
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
          {filtered.map((approval) => (
            <ApprovalCard
              key={approval.approval_id}
              approval={approval}
              onApprove={handleApprove}
              onDeny={handleDeny}
              isApproving={
                processingId === approval.approval_id &&
                processingAction === "approve"
              }
              isDenying={
                processingId === approval.approval_id &&
                processingAction === "deny"
              }
            />
          ))}
        </div>
      </DataContainer>
    </div>
  );
}

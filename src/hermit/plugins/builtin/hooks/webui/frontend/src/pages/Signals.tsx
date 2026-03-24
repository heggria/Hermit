import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { SignalCard } from "@/components/signals/SignalCard";
import { useSignals, useSignalAction } from "@/api/hooks";

type FilterTab = "all" | "pending" | "acted" | "dismissed";

export default function Signals() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<FilterTab>("all");
  const { data, isLoading, error } = useSignals();
  const signalAction = useSignalAction();

  const signals = data?.signals ?? [];

  const counts = useMemo(
    () => ({
      all: signals.length,
      pending: signals.filter((s) => s.disposition === "pending").length,
      acted: signals.filter((s) => s.disposition === "acted").length,
      dismissed: signals.filter((s) => s.disposition === "dismissed").length,
    }),
    [signals]
  );

  const filtered = useMemo(() => {
    if (filter === "all") return signals;
    return signals.filter((s) => s.disposition === filter);
  }, [signals, filter]);

  const handleAct = (signalId: string) => {
    signalAction.mutate({ signalId, action: "act" });
  };

  const handleSuppress = (signalId: string) => {
    signalAction.mutate({ signalId, action: "suppress" });
  };

  const tabs: { key: FilterTab; label: string; count: number }[] = [
    { key: "all", label: t("signals.filterAll"), count: counts.all },
    { key: "pending", label: t("signals.filterPending"), count: counts.pending },
    { key: "acted", label: t("signals.filterActed"), count: counts.acted },
    {
      key: "dismissed",
      label: t("signals.filterDismissed"),
      count: counts.dismissed,
    },
  ];

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {t("signals.title")}
        </h1>
        {counts.pending > 0 && (
          <span className="inline-flex items-center rounded-full bg-primary px-3 py-0.5 text-xs font-semibold text-primary-foreground">
            {t("signals.actionable", { count: counts.pending })}
          </span>
        )}
      </div>

      {/* Filter pills */}
      <div className="flex gap-2">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            className={`rounded-full px-4 py-1.5 text-sm font-medium transition-all ${
              filter === tab.key
                ? "bg-primary text-primary-foreground shadow-sm"
                : "bg-card text-muted-foreground hover:bg-muted border border-border"
            }`}
          >
            {tab.label} ({tab.count})
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {t("signals.loading")}
        </p>
      )}
      {error && (
        <p className="py-12 text-center text-sm text-red-500">
          {t("signals.loadError")}: {(error as Error).message}
        </p>
      )}
      {!isLoading && !error && filtered.length === 0 && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {t("signals.noResults")}
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
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
    </div>
  );
}

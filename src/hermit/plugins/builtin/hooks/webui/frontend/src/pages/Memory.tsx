import { useState, useMemo } from "react";
import { Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MemoryCard } from "@/components/memory/MemoryCard";
import { useMemories } from "@/api/hooks";

type FilterTab = "all" | "active" | "invalidated";

export default function Memory() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterTab>("all");
  const { data, isLoading, error } = useMemories();

  const memories = data?.memories ?? [];

  const counts = useMemo(
    () => ({
      all: memories.length,
      active: memories.filter((m) => m.status === "active").length,
      invalidated: memories.filter((m) => m.status === "invalidated").length,
    }),
    [memories]
  );

  const filtered = useMemo(() => {
    let result = memories;

    if (filter !== "all") {
      result = result.filter((m) => m.status === filter);
    }

    if (search.trim()) {
      const query = search.toLowerCase();
      result = result.filter(
        (m) =>
          m.claim_text.toLowerCase().includes(query) ||
          m.category.toLowerCase().includes(query)
      );
    }

    return result;
  }, [memories, filter, search]);

  const tabs: { key: FilterTab; label: string; count: number }[] = [
    { key: "all", label: t("memory.filterAll"), count: counts.all },
    { key: "active", label: t("memory.filterActive"), count: counts.active },
    {
      key: "invalidated",
      label: t("memory.filterInvalidated"),
      count: counts.invalidated,
    },
  ];

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {t("memory.title")}
        </h1>
      </div>

      {/* Search bar */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("memory.searchPlaceholder")}
          className="w-full rounded-xl border border-border bg-card py-2.5 pl-10 pr-4 text-sm text-foreground placeholder:text-muted-foreground outline-none transition-shadow focus:border-primary focus:ring-2 focus:ring-primary/20"
        />
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
          {t("memory.loading")}
        </p>
      )}
      {error && (
        <p className="py-12 text-center text-sm text-red-500">
          {t("memory.loadError")}: {(error as Error).message}
        </p>
      )}
      {!isLoading && !error && filtered.length === 0 && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {t("memory.noResults")}
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
        {filtered.map((memory) => (
          <MemoryCard key={memory.memory_id} memory={memory} />
        ))}
      </div>
    </div>
  );
}

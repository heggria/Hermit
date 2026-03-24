import { useTranslation } from "react-i18next";
import { useConfigStatus } from "@/api/hooks";
import { PluginList } from "@/components/config/PluginList";

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function SystemStatus() {
  const { t } = useTranslation();
  const { data, isLoading } = useConfigStatus();

  if (isLoading) {
    return (
      <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
        <h3 className="text-base font-semibold text-foreground">
          {t("config.systemStatus")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("config.systemStatusDesc")}
        </p>
        <div className="mt-5 space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-5 w-48 animate-pulse rounded bg-muted"
            />
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
        <h3 className="text-base font-semibold text-foreground">
          {t("config.systemStatus")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("config.systemStatusDesc")}
        </p>
        <p className="mt-5 text-sm text-muted-foreground">
          {t("config.statusError")}
        </p>
      </div>
    );
  }

  const items = [
    { label: t("config.hostname"), value: data.host },
    { label: t("config.port"), value: String(data.port) },
    { label: t("config.pid"), value: String(data.pid) },
    { label: t("config.uptime"), value: formatUptime(data.uptime) },
  ];

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">
        {t("config.systemStatus")}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t("config.systemStatusDesc")}
      </p>
      <dl className="mt-5 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
        {items.map((item) => (
          <div key={item.label}>
            <dt className="text-xs font-medium text-muted-foreground">
              {item.label}
            </dt>
            <dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">
              {item.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default function Config() {
  const { t } = useTranslation();

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {t("config.title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("config.description")}
        </p>
      </div>

      <SystemStatus />

      <PluginList />
    </div>
  );
}

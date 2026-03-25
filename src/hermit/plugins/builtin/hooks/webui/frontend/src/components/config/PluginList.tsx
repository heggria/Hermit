import { useTranslation } from "react-i18next";
import { useConfigPlugins } from "@/api/hooks";

export function PluginList() {
  const { t } = useTranslation();
  const { data, isLoading } = useConfigPlugins();

  if (isLoading) {
    return (
      <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
        <h3 className="text-base font-semibold text-foreground">
          {t("config.plugins")}
        </h3>
        <div className="mt-5 space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-8 animate-pulse rounded bg-muted"
            />
          ))}
        </div>
      </div>
    );
  }

  const plugins = data ?? [];

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="mb-4 text-base font-semibold text-foreground">
        {t("config.plugins")}
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className="pb-3 pr-4 text-left text-xs font-medium text-muted-foreground">
                {t("config.pluginName")}
              </th>
              <th className="pb-3 pr-4 text-left text-xs font-medium text-muted-foreground">
                {t("config.pluginVersion")}
              </th>
              <th className="pb-3 pr-4 text-left text-xs font-medium text-muted-foreground">
                {t("config.pluginDescription")}
              </th>
              <th className="pb-3 text-left text-xs font-medium text-muted-foreground">
                {t("config.pluginType")}
              </th>
            </tr>
          </thead>
          <tbody>
            {plugins.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  className="py-12 text-center text-sm text-muted-foreground"
                >
                  {t("config.noPlugins")}
                </td>
              </tr>
            ) : (
              plugins.map((plugin) => (
                <tr
                  key={plugin.name}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="py-3 pr-4 font-medium text-foreground">
                    {plugin.name}
                  </td>
                  <td className="py-3 pr-4 tabular-nums text-muted-foreground">
                    {plugin.version}
                  </td>
                  <td className="max-w-[300px] truncate py-3 pr-4 text-muted-foreground">
                    {plugin.description}
                  </td>
                  <td className="py-3">
                    {plugin.builtin ? (
                      <span className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                        {t("config.pluginBuiltin")}
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                        {t("config.pluginUser")}
                      </span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

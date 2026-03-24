import { useTranslation } from "react-i18next";
import type { RiskEntry } from "@/types";

interface RiskTableProps {
  readonly entries: readonly RiskEntry[];
}

const RISK_PILL_STYLES: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  medium: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  high: "bg-orange-50 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  critical: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
};

const RESULT_PILL_STYLES: Record<string, string> = {
  succeeded: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  success: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  ok: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  failure: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  error: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  uncertain: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  denied: "bg-muted text-muted-foreground",
  skipped: "bg-muted text-muted-foreground",
};

export function RiskTable({ entries }: RiskTableProps) {
  const { t } = useTranslation();
  const limited = entries.slice(-20);

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="mb-4 text-base font-semibold text-foreground">
        {t("metrics.riskEntries")}
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className="pb-3 pr-4 text-left text-xs font-medium text-muted-foreground">
                {t("metrics.actionType")}
              </th>
              <th className="pb-3 pr-4 text-left text-xs font-medium text-muted-foreground">
                {t("metrics.riskLevel")}
              </th>
              <th className="pb-3 pr-4 text-left text-xs font-medium text-muted-foreground">
                {t("metrics.resultCode")}
              </th>
              <th className="pb-3 text-left text-xs font-medium text-muted-foreground">
                {t("metrics.rollback")}
              </th>
            </tr>
          </thead>
          <tbody>
            {limited.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  className="py-12 text-center text-sm text-muted-foreground"
                >
                  {t("metrics.noRiskEntries")}
                </td>
              </tr>
            ) : (
              limited.map((entry, index) => (
                <tr
                  key={index}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="py-3 pr-4 font-medium text-foreground">
                    {entry.action_type}
                  </td>
                  <td className="py-3 pr-4">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        RISK_PILL_STYLES[(entry.risk_level ?? '').toLowerCase()] ??
                        "bg-muted text-muted-foreground"
                      }`}
                    >
                      {entry.risk_level ?? 'unknown'}
                    </span>
                  </td>
                  <td className="py-3 pr-4">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        RESULT_PILL_STYLES[(entry.result_code ?? '').toLowerCase()] ??
                        "bg-muted text-muted-foreground"
                      }`}
                    >
                      {entry.result_code ?? 'unknown'}
                    </span>
                  </td>
                  <td className="py-3 text-muted-foreground">
                    {entry.rollback_supported
                      ? t("common.yes")
                      : t("common.no")}
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

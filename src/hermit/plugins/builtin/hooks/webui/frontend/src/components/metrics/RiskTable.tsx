import { useTranslation } from "react-i18next";
import { getRiskStyle, getResultCodeStyle } from "@/lib/status-styles";
import type { RiskEntry } from "@/types";

interface RiskTableProps {
  readonly entries: readonly RiskEntry[];
}

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
              limited.map((entry, index) => {
                const riskStyle = getRiskStyle((entry.risk_level ?? '').toLowerCase());
                const resultStyle = getResultCodeStyle((entry.result_code ?? '').toLowerCase());

                return (
                  <tr
                    key={index}
                    className="border-b border-border/50 last:border-0"
                  >
                    <td className="py-3 pr-4 font-medium text-foreground">
                      {entry.action_type}
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${riskStyle.bg} ${riskStyle.text}`}
                      >
                        {entry.risk_level ?? 'unknown'}
                      </span>
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${resultStyle.bg} ${resultStyle.text}`}
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
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

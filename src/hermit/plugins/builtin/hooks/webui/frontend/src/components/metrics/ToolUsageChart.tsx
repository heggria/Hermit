import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { useTranslation } from "react-i18next";

interface ToolUsageChartProps {
  readonly toolUsageCounts: Record<string, number>;
}

export function ToolUsageChart({ toolUsageCounts }: ToolUsageChartProps) {
  const { t } = useTranslation();

  const data = Object.entries(toolUsageCounts)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  if (data.length === 0) {
    return (
      <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
        <h3 className="text-base font-semibold text-foreground">
          {t("metrics.toolUsage")}
        </h3>
        <p className="mt-4 text-sm text-muted-foreground">
          {t("metrics.noToolData")}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="mb-4 text-base font-semibold text-foreground">
        {t("metrics.toolUsageTop")}
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 24, left: 0, bottom: 0 }}
        >
          <XAxis
            type="number"
            allowDecimals={false}
            axisLine={false}
            tickLine={false}
            tick={{ fill: "var(--color-muted-foreground)", fontSize: 12 }}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={120}
            axisLine={false}
            tickLine={false}
            tick={{ fill: "var(--color-foreground)", fontSize: 12 }}
          />
          <Tooltip
            contentStyle={{
              borderRadius: "12px",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-card)",
              color: "var(--color-foreground)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
            }}
          />
          <Bar
            dataKey="count"
            fill="var(--color-primary)"
            radius={[0, 6, 6, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { useTranslation } from "react-i18next";

const COLORS = [
  "var(--color-chart-1)",
  "var(--color-chart-2)",
  "var(--color-chart-3)",
  "var(--color-chart-4)",
  "var(--color-chart-5)",
  "#C47B8E",
  "#6B98C4",
  "#B8A44C",
];

interface ActionClassChartProps {
  readonly distribution: Record<string, number>;
}

export function ActionClassChart({ distribution }: ActionClassChartProps) {
  const { t } = useTranslation();

  const data = Object.entries(distribution)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);

  if (data.length === 0) {
    return (
      <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
        <h3 className="text-base font-semibold text-foreground">
          {t("metrics.actionClass")}
        </h3>
        <p className="mt-4 text-sm text-muted-foreground">
          {t("metrics.noActionData")}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="mb-4 text-base font-semibold text-foreground">
        {t("metrics.actionClass")}
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={100}
            paddingAngle={2}
            dataKey="value"
            nameKey="name"
            stroke="none"
            label={({
              name,
              percent,
            }: {
              name?: string;
              percent?: number;
            }) => `${name ?? ""} (${((percent ?? 0) * 100).toFixed(0)}%)`}
            labelLine={false}
          >
            {data.map((_, index) => (
              <Cell
                key={`cell-${index}`}
                fill={COLORS[index % COLORS.length]}
              />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              borderRadius: "12px",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-card)",
              color: "var(--color-foreground)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
            }}
          />
          <Legend
            wrapperStyle={{
              fontSize: "12px",
              color: "var(--color-muted-foreground)",
            }}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

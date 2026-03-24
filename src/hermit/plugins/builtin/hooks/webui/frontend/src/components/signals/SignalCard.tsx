import { Zap, Ban } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { EvidenceSignal } from "@/types";

interface SignalCardProps {
  signal: EvidenceSignal;
  onAct: (signalId: string) => void;
  onSuppress: (signalId: string) => void;
  isActing: boolean;
}

const RISK_STYLES: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  medium: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  high: "bg-orange-50 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  critical: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
};

const SOURCE_KIND_LABELS: Record<string, Record<string, string>> = {
  en: {
    test_failure: "Test Failure",
    todo_scan: "TODO Marker",
    lint_violation: "Lint Issue",
    security_vuln: "Security",
    coverage_drop: "Coverage",
  },
  zh: {
    test_failure: "测试失败",
    todo_scan: "待办标记",
    lint_violation: "代码规范",
    security_vuln: "安全漏洞",
    coverage_drop: "覆盖率",
  },
};

function getSourceKindLabel(sourceKind: string, lang: string): string {
  const langKey = lang.startsWith("zh") ? "zh" : "en";
  return SOURCE_KIND_LABELS[langKey]?.[sourceKind] ?? sourceKind;
}

export function SignalCard({
  signal,
  onAct,
  onSuppress,
  isActing,
}: SignalCardProps) {
  const { t, i18n } = useTranslation();
  const confidencePct = Math.round(signal.confidence * 100);
  const isPending = signal.disposition === "pending";
  const riskStyle = RISK_STYLES[signal.risk_level] ?? RISK_STYLES.low;
  const sourceLabel = getSourceKindLabel(signal.source_kind, i18n.language);

  return (
    <div className="group rounded-2xl bg-card border border-border p-5 shadow-sm transition-all duration-200 hover:-translate-y-[1px] hover:shadow-md">
      {/* Summary — truncated to 3 lines */}
      <p
        className="text-sm font-medium leading-relaxed text-foreground"
        style={{
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
        title={signal.summary}
      >
        {signal.summary}
      </p>

      {/* Badges row */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
          {sourceLabel}
        </span>
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${riskStyle}`}
        >
          {signal.risk_level}
        </span>
        <span className="text-xs text-muted-foreground">
          {t("signals.confidence", { value: confidencePct })}
        </span>
      </div>

      {/* Suggested goal — truncated to 2 lines */}
      {signal.suggested_goal && (
        <div
          className="mt-3 rounded-xl bg-secondary px-3 py-2 text-xs text-muted-foreground"
          title={signal.suggested_goal}
        >
          <span className="font-medium text-foreground">
            {t("signals.goal")}:{" "}
          </span>
          <span
            style={{
              display: "-webkit-inline-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {signal.suggested_goal}
          </span>
        </div>
      )}

      {/* Actions for pending signals */}
      {isPending && (
        <div className="mt-4 flex items-center gap-2">
          <button
            onClick={() => onAct(signal.signal_id)}
            disabled={isActing}
            className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            <Zap className="size-3" />
            {t("signals.act")}
          </button>
          <button
            onClick={() => onSuppress(signal.signal_id)}
            disabled={isActing}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-4 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
          >
            <Ban className="size-3" />
            {t("signals.suppress")}
          </button>
        </div>
      )}
    </div>
  );
}

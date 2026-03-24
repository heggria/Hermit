import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { FileText } from "lucide-react";
import type { ReceiptRecord } from "@/types";

const RESULT_CODE_STYLES: Record<string, { bg: string; text: string }> = {
  succeeded: { bg: "bg-emerald-50 dark:bg-emerald-950/40", text: "text-emerald-700 dark:text-emerald-300" },
  failed: { bg: "bg-red-50 dark:bg-red-950/40", text: "text-red-600 dark:text-red-400" },
  uncertain: { bg: "bg-amber-50 dark:bg-amber-950/40", text: "text-amber-600 dark:text-amber-400" },
  denied: { bg: "bg-muted", text: "text-muted-foreground" },
};

function getResultStyle(resultCode: string) {
  return (
    RESULT_CODE_STYLES[resultCode] ?? {
      bg: "bg-muted",
      text: "text-muted-foreground",
    }
  );
}

interface ReceiptListProps {
  readonly receipts: ReceiptRecord[];
}

export function ReceiptList({ receipts }: ReceiptListProps) {
  const { t } = useTranslation();

  if (receipts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <FileText className="mb-3 size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {t("taskDetail.receipts.noReceipts")}
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-border/50">
      {/* Table header */}
      <div className="grid grid-cols-[1fr_auto_1.5fr_auto] gap-4 border-b border-border bg-background px-5 py-3 text-xs font-medium text-muted-foreground">
        <div>{t("taskDetail.receipts.actionType")}</div>
        <div>{t("taskDetail.receipts.result")}</div>
        <div>{t("taskDetail.receipts.summary")}</div>
        <div>{t("taskDetail.receipts.rollback")}</div>
      </div>

      {/* Table body */}
      <div className="divide-y divide-border/50">
        {receipts.map((receipt) => {
          const style = getResultStyle(receipt.result_code);

          return (
            <div
              key={receipt.receipt_id}
              className="grid grid-cols-[1fr_auto_1.5fr_auto] items-center gap-4 px-5 py-3.5 transition-colors hover:bg-background"
            >
              {/* Action type */}
              <div className="font-mono text-xs text-foreground">
                {receipt.action_type}
              </div>

              {/* Result badge */}
              <div>
                <span
                  className={cn(
                    "inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium",
                    style.bg,
                    style.text,
                  )}
                >
                  {receipt.result_code}
                </span>
              </div>

              {/* Summary */}
              <div className="truncate text-sm text-muted-foreground">
                {receipt.result_summary || "--"}
              </div>

              {/* Rollback */}
              <div className="flex items-center gap-1.5">
                {receipt.rollback_supported ? (
                  <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                    {t("taskDetail.receipts.supported")}
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground/40">--</span>
                )}
                {receipt.rollback_supported &&
                  receipt.rollback_status !== "none" && (
                    <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                      {receipt.rollback_status}
                    </span>
                  )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

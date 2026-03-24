import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ChevronDown,
  ChevronRight,
  Download,
  ShieldCheck,
  ShieldX,
  Shield,
  Hash,
} from "lucide-react";

interface ProofViewerProps {
  readonly proof: Record<string, unknown> | undefined;
  readonly isLoading: boolean;
}

export function ProofViewer({ proof, isLoading }: ProofViewerProps) {
  const { t } = useTranslation();
  const [showRawJson, setShowRawJson] = useState(false);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="flex items-center gap-3">
          <div className="size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">
            {t("taskDetail.proof.loading")}
          </p>
        </div>
      </div>
    );
  }

  if (!proof) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <Shield className="mb-3 size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">
          {t("taskDetail.proof.noData")}
        </p>
      </div>
    );
  }

  const chainValid = (proof.chain_valid as boolean) ?? null;
  const checkedHashes = (proof.checked_hashes as number) ?? 0;
  const summary = proof.summary as Record<string, unknown> | undefined;

  function handleExportProof() {
    const blob = new Blob([JSON.stringify(proof, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `proof-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-5">
      {/* Chain verification status card */}
      <div className="rounded-2xl bg-card p-6 shadow-sm ring-1 ring-border/50">
        <div className="flex items-center gap-4">
          {/* Big icon */}
          <div
            className={`flex size-14 items-center justify-center rounded-2xl ${
              chainValid === true
                ? "bg-emerald-50 dark:bg-emerald-950/40"
                : chainValid === false
                  ? "bg-red-50 dark:bg-red-950/40"
                  : "bg-muted"
            }`}
          >
            {chainValid === true ? (
              <ShieldCheck className="size-7 text-emerald-600 dark:text-emerald-400" />
            ) : chainValid === false ? (
              <ShieldX className="size-7 text-red-500 dark:text-red-400" />
            ) : (
              <Shield className="size-7 text-muted-foreground" />
            )}
          </div>

          <div>
            <h3 className="text-base font-semibold text-foreground">
              {t("taskDetail.proof.chainVerification")}
            </h3>
            <div className="mt-1 flex items-center gap-4">
              {/* Status badge */}
              <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  chainValid === true
                    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                    : chainValid === false
                      ? "bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400"
                      : "bg-muted text-muted-foreground"
                }`}
              >
                {chainValid === true
                  ? t("taskDetail.proof.valid")
                  : chainValid === false
                    ? t("taskDetail.proof.invalid")
                    : t("taskDetail.proof.unknown")}
              </span>

              {/* Hash count */}
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Hash className="size-3.5" />
                {t("taskDetail.proof.checkedHashes")} {checkedHashes}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Summary section */}
      {summary && (
        <div className="rounded-2xl bg-card p-6 shadow-sm ring-1 ring-border/50">
          <h3 className="mb-4 text-sm font-semibold text-foreground">
            {t("taskDetail.proof.proofSummary")}
          </h3>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            {Object.entries(summary).map(([key, value]) => (
              <div key={key}>
                <dt className="text-xs text-muted-foreground/60">{key}</dt>
                <dd className="mt-0.5 break-all font-mono text-xs text-foreground">
                  {typeof value === "object"
                    ? JSON.stringify(value)
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* Collapsible raw JSON */}
      <div className="rounded-2xl bg-card shadow-sm ring-1 ring-border/50">
        <button
          type="button"
          onClick={() => setShowRawJson((prev) => !prev)}
          className="flex w-full items-center gap-2 px-6 py-4 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          {showRawJson ? (
            <ChevronDown className="size-4" />
          ) : (
            <ChevronRight className="size-4" />
          )}
          {t("taskDetail.proof.rawJson")}
        </button>

        {showRawJson && (
          <div className="border-t border-border px-6 py-4">
            <ScrollArea className="h-[400px] rounded-xl bg-background p-4">
              <pre className="font-mono text-xs leading-relaxed text-foreground">
                {JSON.stringify(proof, null, 2)}
              </pre>
            </ScrollArea>
          </div>
        )}
      </div>

      {/* Export button */}
      <button
        type="button"
        onClick={handleExportProof}
        className="inline-flex items-center gap-2 rounded-lg border border-primary px-4 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/5"
      >
        <Download className="size-4" />
        {t("taskDetail.proof.exportProof")}
      </button>
    </div>
  );
}

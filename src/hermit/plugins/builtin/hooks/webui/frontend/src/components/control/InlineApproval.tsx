import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useApproveMutation, useDenyMutation } from "@/api/hooks";
import { cn } from "@/lib/utils";
import { Check, Loader2, Terminal, X } from "lucide-react";
import type { ApprovalRecord } from "@/types";

// ---------------------------------------------------------------------------
// Component — compact single-line approval for in-card display
// ---------------------------------------------------------------------------

interface InlineApprovalProps {
  readonly approval: ApprovalRecord;
}

export function InlineApproval({ approval }: InlineApprovalProps) {
  const { t } = useTranslation();
  const approveMutation = useApproveMutation();
  const denyMutation = useDenyMutation();
  const [actionInFlight, setActionInFlight] = useState<
    "approve" | "deny" | null
  >(null);

  const action = approval.requested_action;
  const toolName =
    (action.tool_name as string) ?? (action.tool as string) ?? "unknown";

  const isProcessing = actionInFlight !== null;

  async function handleApprove() {
    setActionInFlight("approve");
    try {
      await approveMutation.mutateAsync(approval.approval_id);
    } finally {
      setActionInFlight(null);
    }
  }

  async function handleDeny() {
    setActionInFlight("deny");
    try {
      await denyMutation.mutateAsync({
        approvalId: approval.approval_id,
        reason: "",
      });
    } finally {
      setActionInFlight(null);
    }
  }

  return (
    <div className="flex items-center gap-2 rounded-lg border border-amber-300/60 bg-amber-50/40 px-2.5 py-1.5 dark:border-amber-700/40 dark:bg-amber-950/20">
      {/* Tool icon + name */}
      <Terminal className="size-3 flex-shrink-0 text-amber-600 dark:text-amber-400" />
      <code className="min-w-0 truncate font-mono text-[11px] font-medium text-foreground">
        {toolName}
      </code>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Approve */}
      <button
        type="button"
        onClick={handleApprove}
        disabled={isProcessing}
        className={cn(
          "inline-flex h-6 items-center gap-1 rounded-md bg-primary px-2 text-[11px] font-medium text-primary-foreground shadow-sm transition-all hover:bg-primary/90",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        {actionInFlight === "approve" ? (
          <Loader2 className="size-2.5 animate-spin" />
        ) : (
          <Check className="size-2.5" />
        )}
        {t("approvals.approve")}
      </button>

      {/* Deny */}
      <button
        type="button"
        onClick={handleDeny}
        disabled={isProcessing}
        className={cn(
          "inline-flex h-6 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted-foreground transition-all hover:text-red-500 dark:hover:text-red-400",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        {actionInFlight === "deny" ? (
          <Loader2 className="size-2.5 animate-spin" />
        ) : (
          <X className="size-2.5" />
        )}
        {t("approvals.deny")}
      </button>
    </div>
  );
}

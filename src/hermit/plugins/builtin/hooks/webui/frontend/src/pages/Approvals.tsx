import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { useApprovals, useApproveMutation, useDenyMutation } from "@/api/hooks";
import { ShieldAlert } from "lucide-react";

export default function Approvals() {
  const { t } = useTranslation();
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [processingAction, setProcessingAction] = useState<
    "approve" | "deny" | null
  >(null);

  const approvalsQuery = useApprovals("pending");
  const approveMutation = useApproveMutation();
  const denyMutation = useDenyMutation();

  const approvals = approvalsQuery.data?.approvals ?? [];

  async function handleApprove(approvalId: string) {
    setProcessingId(approvalId);
    setProcessingAction("approve");
    try {
      await approveMutation.mutateAsync(approvalId);
    } finally {
      setProcessingId(null);
      setProcessingAction(null);
    }
  }

  async function handleDeny(approvalId: string, reason: string) {
    setProcessingId(approvalId);
    setProcessingAction("deny");
    try {
      await denyMutation.mutateAsync({ approvalId, reason });
    } finally {
      setProcessingId(null);
      setProcessingAction(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 pb-12">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold text-foreground">
          {t("approvals.title")}
        </h1>
        {approvals.length > 0 && (
          <span className="inline-flex min-w-[22px] items-center justify-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
            {approvals.length}
          </span>
        )}
      </div>

      {/* Loading state */}
      {approvalsQuery.isLoading && (
        <div className="flex items-center justify-center py-16">
          <div className="flex items-center gap-3">
            <div className="size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">{t("approvals.loading")}</p>
          </div>
        </div>
      )}

      {/* Error state */}
      {approvalsQuery.isError && (
        <div className="flex items-center justify-center py-16">
          <p className="text-sm text-rose-600">
            {t("approvals.loadError", {
              message: approvalsQuery.error.message,
            })}
          </p>
        </div>
      )}

      {/* Empty state */}
      {!approvalsQuery.isLoading &&
        !approvalsQuery.isError &&
        approvals.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16">
            <div className="mb-4 flex size-16 items-center justify-center rounded-2xl bg-muted">
              <ShieldAlert className="size-8 text-muted-foreground/60" />
            </div>
            <p className="text-sm font-medium text-foreground">
              {t("approvals.noApprovals")}
            </p>
          </div>
        )}

      {/* Approval cards grid */}
      <div className="grid gap-4 sm:grid-cols-1 lg:grid-cols-2">
        {approvals.map((approval) => (
          <ApprovalCard
            key={approval.approval_id}
            approval={approval}
            onApprove={handleApprove}
            onDeny={handleDeny}
            isApproving={
              processingId === approval.approval_id &&
              processingAction === "approve"
            }
            isDenying={
              processingId === approval.approval_id &&
              processingAction === "deny"
            }
          />
        ))}
      </div>
    </div>
  );
}

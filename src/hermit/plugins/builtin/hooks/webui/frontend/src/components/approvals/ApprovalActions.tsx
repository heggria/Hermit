import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Check, X, Loader2 } from "lucide-react";

interface ApprovalActionsProps {
  readonly approvalId: string;
  readonly onApprove: (approvalId: string) => Promise<void>;
  readonly onDeny: (approvalId: string, reason: string) => Promise<void>;
  readonly isApproving: boolean;
  readonly isDenying: boolean;
}

export function ApprovalActions({
  approvalId,
  onApprove,
  onDeny,
  isApproving,
  isDenying,
}: ApprovalActionsProps) {
  const { t } = useTranslation();
  const [denyDialogOpen, setDenyDialogOpen] = useState(false);
  const [denyReason, setDenyReason] = useState("");

  const isProcessing = isApproving || isDenying;

  async function handleApprove() {
    await onApprove(approvalId);
  }

  async function handleDeny() {
    await onDeny(approvalId, denyReason);
    setDenyDialogOpen(false);
    setDenyReason("");
  }

  return (
    <div className="flex items-center gap-2">
      {/* Approve button -- warm terracotta filled */}
      <button
        type="button"
        onClick={handleApprove}
        disabled={isProcessing}
        className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isApproving ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Check className="size-3.5" />
        )}
        {t("approvals.approve")}
      </button>

      {/* Deny button -- ghost/outline */}
      <Dialog open={denyDialogOpen} onOpenChange={setDenyDialogOpen}>
        <DialogTrigger asChild>
          <button
            type="button"
            disabled={isProcessing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-all hover:border-red-400 hover:text-red-500 dark:hover:border-red-500 dark:hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isDenying ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <X className="size-3.5" />
            )}
            {t("approvals.deny")}
          </button>
        </DialogTrigger>
        <DialogContent className="rounded-2xl border-0 bg-card shadow-lg ring-1 ring-border sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold text-foreground">
              {t("approvals.denyDialog.title")}
            </DialogTitle>
            <DialogDescription className="text-sm text-muted-foreground">
              {t("approvals.denyDialog.description")}
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder={t("approvals.denyDialog.placeholder")}
            value={denyReason}
            onChange={(e) => setDenyReason(e.target.value)}
            rows={3}
            className="rounded-xl border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:ring-primary/20"
          />
          <DialogFooter className="gap-2">
            <button
              type="button"
              onClick={() => setDenyDialogOpen(false)}
              className="inline-flex items-center rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
            >
              {t("approvals.denyDialog.cancel")}
            </button>
            <button
              type="button"
              onClick={handleDeny}
              disabled={isDenying}
              className="inline-flex items-center gap-1.5 rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-600 dark:bg-red-600 dark:hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isDenying && <Loader2 className="size-3.5 animate-spin" />}
              {t("approvals.denyDialog.confirmDeny")}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

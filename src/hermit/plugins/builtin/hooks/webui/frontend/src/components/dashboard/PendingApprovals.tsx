import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Check, X, ShieldCheck } from "lucide-react";
import { useApprovals, useApproveMutation, useDenyMutation } from "@/api/hooks";
import { formatTimeAgo } from "@/lib/format";
import { useTranslation } from "react-i18next";
import type { ApprovalRecord } from "@/types";

function extractToolName(requestedAction: Record<string, unknown>): string {
  const toolName = requestedAction["tool_name"];
  if (typeof toolName === "string") return toolName;
  const tool = requestedAction["tool"];
  if (typeof tool === "string") return tool;
  return "unknown";
}

function extractActionSummary(
  requestedAction: Record<string, unknown>,
  fallback: string,
): string {
  const desc = requestedAction["description"];
  if (typeof desc === "string") return desc;

  const input = requestedAction["input"];
  if (input != null && typeof input === "object") {
    const keys = Object.keys(input).slice(0, 3);
    return keys.join(", ");
  }

  return fallback;
}

interface ApprovalItemProps {
  readonly approval: ApprovalRecord;
  readonly onApprove: (id: string) => void;
  readonly onDeny: (id: string) => void;
  readonly isApproving: boolean;
  readonly isDenying: boolean;
}

function ApprovalItem({
  approval,
  onApprove,
  onDeny,
  isApproving,
  isDenying,
}: ApprovalItemProps) {
  const { t } = useTranslation();
  const toolName = extractToolName(approval.requested_action);
  const actionSummary = extractActionSummary(
    approval.requested_action,
    t("dashboard.pendingApprovals.pendingReview"),
  );

  return (
    <div className="flex items-start justify-between gap-3 rounded-xl p-3 transition-colors hover:bg-accent/50">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
            {toolName}
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {approval.task_id.slice(0, 8)}
          </span>
        </div>
        <p className="truncate text-sm text-foreground">{actionSummary}</p>
        {approval.requested_at != null && (
          <p className="text-xs text-muted-foreground">
            {formatTimeAgo(approval.requested_at)}
          </p>
        )}
      </div>
      <div className="flex shrink-0 gap-2">
        <Button
          size="sm"
          className="h-7 rounded-full bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          disabled={isApproving || isDenying}
          onClick={() => onApprove(approval.approval_id)}
        >
          <Check className="mr-1 size-3" />
          {t("approvals.approve")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 rounded-full px-3 text-xs font-medium"
          disabled={isApproving || isDenying}
          onClick={() => onDeny(approval.approval_id)}
        >
          <X className="mr-1 size-3" />
          {t("approvals.deny")}
        </Button>
      </div>
    </div>
  );
}

export function PendingApprovals() {
  const { data, isLoading } = useApprovals("pending", 10);
  const approveMutation = useApproveMutation();
  const denyMutation = useDenyMutation();
  const { t } = useTranslation();

  const approvals = data?.approvals ?? [];

  return (
    <div
      className="animate-slide-up max-h-[500px] flex flex-col rounded-2xl bg-card p-6 shadow-sm ring-1 ring-border/50"
      style={{ animationDelay: "0.2s" }}
    >
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-base font-semibold text-foreground">
          {t("dashboard.pendingApprovals.title")}
        </h3>
        {approvals.length > 0 && (
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-medium text-primary-foreground">
            {approvals.length}
          </span>
        )}
      </div>
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-14 animate-pulse rounded-xl bg-muted"
            />
          ))}
        </div>
      ) : approvals.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <ShieldCheck className="mb-3 size-10 text-muted-foreground/40" />
          <p className="text-sm font-medium text-muted-foreground">
            {t("dashboard.pendingApprovals.noApprovals")}
          </p>
          <p className="mt-1 text-xs text-muted-foreground/70">
            {t("dashboard.pendingApprovals.noApprovals")}
          </p>
        </div>
      ) : (
        <ScrollArea className="h-[350px] flex-1 overflow-hidden">
          <div className="space-y-1">
            {approvals.map((approval) => (
              <ApprovalItem
                key={approval.approval_id}
                approval={approval}
                onApprove={(id) => approveMutation.mutate(id)}
                onDeny={(id) => denyMutation.mutate({ approvalId: id })}
                isApproving={approveMutation.isPending}
                isDenying={denyMutation.isPending}
              />
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}

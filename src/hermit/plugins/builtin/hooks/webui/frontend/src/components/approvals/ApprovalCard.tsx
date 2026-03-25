import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { formatTimeAgo } from "@/lib/format";
import { getRiskStyle } from "@/lib/status-styles";
import { AlertTriangle, Terminal } from "lucide-react";
import type { ApprovalRecord } from "@/types";

interface ApprovalCardProps {
  readonly approval: ApprovalRecord;
  readonly taskTitle?: string;
  readonly onApprove: (approvalId: string) => Promise<void>;
  readonly onDeny: (approvalId: string, reason: string) => Promise<void>;
  readonly isApproving: boolean;
  readonly isDenying: boolean;
}

export function ApprovalCard({
  approval,
  taskTitle,
  onApprove,
  onDeny,
  isApproving,
  isDenying,
}: ApprovalCardProps) {
  const { t } = useTranslation();
  const action = approval.requested_action;
  const toolName =
    (action.tool_name as string) ?? (action.tool as string) ?? "unknown";
  const toolInput = action.tool_input ?? action.input ?? action.arguments ?? {};
  const inputSummary =
    typeof toolInput === "string" ? toolInput : JSON.stringify(toolInput);
  const truncatedInput =
    inputSummary.length > 200
      ? `${inputSummary.slice(0, 200)}...`
      : inputSummary;
  const riskLevel = (action.risk_level as string) ?? null;

  return (
    <div className="flex flex-col rounded-2xl bg-card shadow-sm ring-1 ring-border/50 transition-shadow hover:shadow-md">
      {/* Header */}
      <div className="flex items-center gap-2.5 px-5 pt-5 pb-3">
        <Terminal className="size-4 text-muted-foreground/60" />
        <code className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-xs font-medium text-primary">
          {toolName}
        </code>
        {riskLevel && (
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${getRiskStyle(riskLevel).bg} ${getRiskStyle(riskLevel).text}`}
          >
            <AlertTriangle className="size-3" />
            {riskLevel}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="space-y-3 px-5 pb-4">
        {/* Task link */}
        <div className="text-sm">
          <span className="text-muted-foreground/60">{t("approvals.card.task")}</span>
          <Link
            to={`/`}
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            {taskTitle ?? approval.task_id.slice(0, 8)}
          </Link>
        </div>

        {/* Tool input */}
        <div className="rounded-xl bg-background p-3">
          <pre className="whitespace-pre-wrap break-all font-mono text-xs leading-relaxed text-muted-foreground">
            {truncatedInput}
          </pre>
        </div>

        {/* Timestamp */}
        {approval.requested_at && (
          <p className="text-xs text-muted-foreground/60">
            {t("approvals.card.requested", {
              time: formatTimeAgo(approval.requested_at),
            })}
          </p>
        )}
      </div>

      {/* Actions footer */}
      <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
        <ApprovalActions
          approvalId={approval.approval_id}
          onApprove={onApprove}
          onDeny={onDeny}
          isApproving={isApproving}
          isDenying={isDenying}
        />
      </div>
    </div>
  );
}

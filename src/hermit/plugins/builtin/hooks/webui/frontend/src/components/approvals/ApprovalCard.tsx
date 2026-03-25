import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { formatTimeAgo } from "@/lib/format";
import { getRiskStyle } from "@/lib/status-styles";
import { AlertTriangle, Terminal, Clock } from "lucide-react";
import type { ApprovalRecord } from "@/types";

interface ApprovalCardProps {
  readonly approval: ApprovalRecord;
  readonly taskTitle?: string;
  readonly onApprove: (approvalId: string) => Promise<void>;
  readonly onDeny: (approvalId: string, reason: string) => Promise<void>;
  readonly isApproving: boolean;
  readonly isDenying: boolean;
}

/** Risk level -> left accent border color. */
function riskAccent(risk: string | null): string {
  switch (risk) {
    case "critical":
      return "border-l-red-500 dark:border-l-red-400";
    case "high":
      return "border-l-orange-500 dark:border-l-orange-400";
    case "medium":
      return "border-l-amber-500 dark:border-l-amber-400";
    case "low":
      return "border-l-emerald-500 dark:border-l-emerald-400";
    default:
      return "border-l-muted-foreground/30";
  }
}

/** Status badge styles. */
function statusStyle(status: string): string {
  switch (status) {
    case "pending":
      return "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
    case "approved":
      return "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
    case "denied":
      return "bg-red-50 text-red-700 dark:bg-red-900/40 dark:text-red-300";
    default:
      return "bg-muted text-muted-foreground";
  }
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
  const isPending = approval.status === "pending";

  return (
    <div
      className={`group relative overflow-hidden rounded-xl border-l-[3px] bg-card border border-border shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md ${riskAccent(riskLevel)}`}
    >
      <div className="p-4 space-y-3">
        {/* Header: tool name + risk + status */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-1.5">
            <Terminal className="size-3.5 text-muted-foreground/50" />
            <code className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-xs font-medium text-primary">
              {toolName}
            </code>
          </div>
          {riskLevel && (
            <span
              className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold ${getRiskStyle(riskLevel).bg} ${getRiskStyle(riskLevel).text}`}
            >
              <AlertTriangle className="size-3" />
              {riskLevel}
            </span>
          )}
          <span
            className={`ml-auto inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium ${statusStyle(approval.status)}`}
          >
            {approval.status}
          </span>
        </div>

        {/* Tool input preview */}
        <div className="rounded-lg bg-secondary/60 px-3 py-2">
          <pre className="whitespace-pre-wrap break-all font-mono text-xs leading-relaxed text-muted-foreground line-clamp-4">
            {truncatedInput}
          </pre>
        </div>

        {/* Footer: task link + time + actions */}
        <div className="flex items-center justify-between pt-0.5">
          <div className="flex items-center gap-3 text-[11px]">
            <Link
              to="/"
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              {taskTitle ?? approval.task_id.slice(0, 8)}
            </Link>
            {approval.requested_at && (
              <span className="flex items-center gap-1 text-muted-foreground/50 tabular-nums">
                <Clock className="size-3" />
                {formatTimeAgo(approval.requested_at)}
              </span>
            )}
          </div>

          {isPending && (
            <ApprovalActions
              approvalId={approval.approval_id}
              onApprove={onApprove}
              onDeny={onDeny}
              isApproving={isApproving}
              isDenying={isDenying}
            />
          )}
        </div>
      </div>

      {/* Hover shimmer */}
      <div className="pointer-events-none absolute inset-0 rounded-xl opacity-0 transition-opacity duration-300 group-hover:opacity-100 bg-gradient-to-br from-primary/[0.02] to-transparent" />
    </div>
  );
}

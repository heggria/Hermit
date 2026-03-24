import { useNavigate } from "react-router-dom";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TaskStatusBadge } from "@/components/tasks/TaskStatusBadge";
import { formatTimeAgo, formatTokenCount } from "@/lib/format";
import { useTranslation } from "react-i18next";
import type { TaskRecord } from "@/types";

interface TaskTableProps {
  readonly tasks: readonly TaskRecord[];
}

export function TaskTable({ tasks }: TaskTableProps) {
  const navigate = useNavigate();
  const { t } = useTranslation();

  return (
    <div className="animate-fade-in overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-border/50">
      <Table>
        <TableHeader>
          <TableRow className="border-border/50 hover:bg-transparent">
            <TableHead className="pl-5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.title")}
            </TableHead>
            <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.status")}
            </TableHead>
            <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.priority")}
            </TableHead>
            <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.source")}
            </TableHead>
            <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.created")}
            </TableHead>
            <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.updated")}
            </TableHead>
            <TableHead className="pr-5 text-right text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("tasks.table.tokensUsed")}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasks.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={7}
                className="h-28 text-center text-muted-foreground"
              >
                {t("tasks.noTasks")}
              </TableCell>
            </TableRow>
          ) : (
            tasks.map((task) => (
              <TableRow
                key={task.task_id}
                className="cursor-pointer border-border/30 transition-colors hover:bg-accent/50"
                onClick={() => navigate(`/`)}
              >
                <TableCell className="max-w-[300px] truncate py-4 pl-5">
                  <span className="font-medium text-foreground">
                    {task.title}
                  </span>
                </TableCell>
                <TableCell className="py-4">
                  <TaskStatusBadge status={task.status} />
                </TableCell>
                <TableCell className="py-4 text-muted-foreground">
                  {task.priority}
                </TableCell>
                <TableCell className="py-4 text-sm text-muted-foreground">
                  {task.source_channel}
                </TableCell>
                <TableCell className="py-4 text-sm text-muted-foreground">
                  {formatTimeAgo(task.created_at)}
                </TableCell>
                <TableCell className="py-4 text-sm text-muted-foreground">
                  {formatTimeAgo(task.updated_at)}
                </TableCell>
                <TableCell className="py-4 pr-5 text-right tabular-nums text-muted-foreground">
                  {formatTokenCount(task.budget_tokens_used)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

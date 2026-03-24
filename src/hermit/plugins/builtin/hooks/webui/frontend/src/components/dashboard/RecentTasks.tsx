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
import { useTaskList } from "@/api/hooks";
import { formatTimeAgo } from "@/lib/format";
import { useTranslation } from "react-i18next";

export function RecentTasks() {
  const { data, isLoading } = useTaskList(undefined, 10);
  const navigate = useNavigate();
  const { t } = useTranslation();

  const tasks = data?.tasks ?? [];

  return (
    <div className="animate-slide-up rounded-2xl bg-card p-6 shadow-sm ring-1 ring-border/50"
         style={{ animationDelay: "0.1s" }}>
      <h3 className="mb-4 text-base font-semibold text-foreground">
        {t("dashboard.recentTasks.title")}
      </h3>
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="h-10 animate-pulse rounded-lg bg-muted"
            />
          ))}
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="border-border/50 hover:bg-transparent">
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {t("tasks.table.title")}
              </TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {t("tasks.table.status")}
              </TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {t("tasks.table.priority")}
              </TableHead>
              <TableHead className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {t("tasks.table.created")}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="h-20 text-center text-muted-foreground"
                >
                  {t("dashboard.recentTasks.noTasks")}
                </TableCell>
              </TableRow>
            ) : (
              tasks.map((task) => (
                <TableRow
                  key={task.task_id}
                  className="cursor-pointer border-border/30 transition-colors hover:bg-accent/50"
                  onClick={() => navigate(`/`)}
                >
                  <TableCell className="max-w-[220px] truncate py-3">
                    <span className="font-medium text-foreground hover:underline">
                      {task.title}
                    </span>
                  </TableCell>
                  <TableCell className="py-3">
                    <TaskStatusBadge status={task.status} />
                  </TableCell>
                  <TableCell className="py-3 text-muted-foreground">
                    {task.priority}
                  </TableCell>
                  <TableCell className="py-3 text-sm text-muted-foreground">
                    {formatTimeAgo(task.created_at)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

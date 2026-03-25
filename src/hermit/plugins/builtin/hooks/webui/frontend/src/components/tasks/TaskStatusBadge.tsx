import { cn } from "@/lib/utils";
import { getStatusStyle } from "@/lib/status-styles";

interface TaskStatusBadgeProps {
  readonly status: string;
  readonly className?: string;
}

export function TaskStatusBadge({ status, className }: TaskStatusBadgeProps) {
  const style = getStatusStyle(status);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide transition-colors",
        style.bg,
        style.text,
        className,
      )}
    >
      {style.pulse && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-40" />
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {status}
    </span>
  );
}

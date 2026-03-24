import { cn } from "@/lib/utils";

const STATUS_CONFIG: Record<string, { readonly className: string; readonly pulse?: boolean }> = {
  queued: {
    className:
      "bg-stone-100 text-stone-600 dark:bg-stone-800/50 dark:text-stone-400",
  },
  running: {
    className:
      "bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400",
    pulse: true,
  },
  blocked: {
    className:
      "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  },
  completed: {
    className:
      "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  },
  failed: {
    className:
      "bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-400",
  },
  cancelled: {
    className:
      "bg-stone-50 text-stone-400 dark:bg-stone-800/30 dark:text-stone-500",
  },
  reconciling: {
    className:
      "bg-violet-50 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400",
  },
};

interface TaskStatusBadgeProps {
  readonly status: string;
  readonly className?: string;
}

export function TaskStatusBadge({ status, className }: TaskStatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.queued;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide transition-colors",
        config.className,
        className,
      )}
    >
      {config.pulse && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-40" />
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {status}
    </span>
  );
}

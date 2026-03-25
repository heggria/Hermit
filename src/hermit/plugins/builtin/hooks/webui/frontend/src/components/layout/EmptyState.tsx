import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  /** Icon element */
  readonly icon: ReactNode;
  /** Primary text */
  readonly title: string;
  /** Optional secondary text or action */
  readonly subtitle?: ReactNode;
  /** Layout variant */
  readonly layout?: 'centered' | 'horizontal';
}

export function EmptyState({
  icon,
  title,
  subtitle,
  layout = 'centered',
}: EmptyStateProps) {
  if (layout === 'horizontal') {
    return (
      <div className="flex items-center gap-3 py-8">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          {icon}
        </div>
        <div>
          <p className="text-sm text-muted-foreground">{title}</p>
          {subtitle && (
            <div className="mt-0.5 text-xs text-muted-foreground/60">
              {subtitle}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className={cn('text-muted-foreground/40')}>{icon}</div>
      <p className="mt-3 text-sm text-muted-foreground">{title}</p>
      {subtitle && (
        <div className="mt-1 text-xs text-muted-foreground/60">{subtitle}</div>
      )}
    </div>
  );
}

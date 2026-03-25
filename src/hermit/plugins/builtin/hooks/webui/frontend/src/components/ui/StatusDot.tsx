import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { getStatusStyle } from '@/lib/status-styles';

interface StatusDotProps {
  /** Task status key (running, blocked, completed, etc.) */
  readonly status: string;
  /** Whether to show the status label text */
  readonly showLabel?: boolean;
  /** Dot size variant */
  readonly size?: 'sm' | 'default';
}

export function StatusDot({
  status,
  showLabel = true,
  size = 'default',
}: StatusDotProps) {
  const { t } = useTranslation();
  const style = getStatusStyle(status);
  const dotSize = size === 'sm' ? 'size-1.5' : 'size-2';

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('relative flex shrink-0', dotSize)}>
        {style.pulse && (
          <span
            className={cn(
              'absolute inline-flex size-full animate-ping rounded-full opacity-40',
              style.dot,
            )}
          />
        )}
        <span
          className={cn('relative inline-flex rounded-full', dotSize, style.dot)}
        />
      </span>
      {showLabel && (
        <span
          className={cn(
            'font-medium',
            style.text,
          )}
        >
          {t(`common.status.${status}`, status)}
        </span>
      )}
    </span>
  );
}

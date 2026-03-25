import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';

interface PageHeaderProps {
  /** Page title */
  readonly title: string;
  /** Optional subtitle below the title */
  readonly subtitle?: string;
  /** Primary action button */
  readonly action?: {
    label: string;
    icon?: ReactNode;
    onClick: () => void;
    disabled?: boolean;
  };
  /** Extra content rendered after the action */
  readonly extra?: ReactNode;
}

export function PageHeader({ title, subtitle, action, extra }: PageHeaderProps) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-foreground">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
        )}
      </div>
      <div className="flex items-center gap-2">
        {action && (
          <Button onClick={action.onClick} size="sm" disabled={action.disabled}>
            {action.icon}
            {action.label}
          </Button>
        )}
        {extra}
      </div>
    </div>
  );
}

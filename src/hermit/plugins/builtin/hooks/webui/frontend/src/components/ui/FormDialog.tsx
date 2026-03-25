import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

interface FormDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  /** Dialog title */
  readonly title: string;
  /** Optional description below the title */
  readonly description?: string;
  /** Whether a mutation is in progress */
  readonly isPending?: boolean;
  /** Error message to display as a banner */
  readonly error?: string;
  /** Submit handler */
  readonly onSubmit: () => void;
  /** Label for the submit button (idle state) */
  readonly submitLabel: string;
  /** Label for the submit button (pending state) */
  readonly pendingLabel: string;
  /** Form field content */
  readonly children: ReactNode;
  /** Max width class for DialogContent (default: "sm:max-w-md") */
  readonly maxWidth?: string;
  /** Extra footer content rendered before the cancel/submit buttons (e.g. delete button) */
  readonly footer?: ReactNode;
}

export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  isPending = false,
  error,
  onSubmit,
  submitLabel,
  pendingLabel,
  children,
  maxWidth = 'sm:max-w-md',
  footer,
}: FormDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={maxWidth}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>

        <div className="space-y-4">
          {error && (
            <div className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
          {children}
        </div>

        <DialogFooter>
          {footer}
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            {t('common.cancel')}
          </Button>
          <Button onClick={onSubmit} disabled={isPending}>
            {isPending ? pendingLabel : submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

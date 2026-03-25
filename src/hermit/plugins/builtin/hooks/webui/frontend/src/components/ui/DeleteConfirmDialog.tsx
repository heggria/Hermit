import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';

interface DeleteConfirmDialogProps {
  /** Whether the dialog is open */
  readonly open: boolean;
  /** Close handler */
  readonly onOpenChange: (open: boolean) => void;
  /** Name of the item being deleted (shown in confirmation message) */
  readonly itemName: string;
  /** Callback when user confirms deletion */
  readonly onConfirm: () => void;
  /** Whether the delete operation is in progress */
  readonly isLoading?: boolean;
  /** Custom dialog title (defaults to common.delete) */
  readonly title?: string;
  /** Custom confirmation message */
  readonly description?: string;
  /** Custom confirm button label (defaults to common.delete) */
  readonly confirmLabel?: string;
}

export function DeleteConfirmDialog({
  open,
  onOpenChange,
  itemName,
  onConfirm,
  isLoading = false,
  title,
  description,
  confirmLabel,
}: DeleteConfirmDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title ?? t('common.delete')}</DialogTitle>
          <DialogDescription>
            {description ??
              t('common.deleteConfirm', { name: itemName })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isLoading}
          >
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={isLoading}
          >
            {isLoading ? t('common.loading') : (confirmLabel ?? t('common.delete'))}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

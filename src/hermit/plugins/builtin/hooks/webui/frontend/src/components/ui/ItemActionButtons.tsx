import { Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ItemActionButtonsProps {
  readonly onEdit?: () => void;
  readonly onDelete?: () => void;
  readonly className?: string;
}

export function ItemActionButtons({
  onEdit,
  onDelete,
  className,
}: ItemActionButtonsProps) {
  if (!onEdit && !onDelete) return null;

  return (
    <div className={className ?? 'flex items-center gap-0.5'}>
      {onEdit && (
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={(e) => {
            e.stopPropagation();
            onEdit();
          }}
          aria-label="Edit"
        >
          <Pencil />
        </Button>
      )}
      {onDelete && (
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          aria-label="Delete"
        >
          <Trash2 className="text-destructive" />
        </Button>
      )}
    </div>
  );
}

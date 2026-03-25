// Dialog for creating a new team.

import { useCallback } from 'react';
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
import { Input } from '@/components/ui/input';
import { useCreateTeam } from '@/api/hooks';
import { useFormDialog } from '@/hooks/useFormDialog';

interface CreateTeamDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly defaultProgramId?: string;
}

const DEFAULT_ROLE_ASSEMBLY = {
  executor: { role: 'executor', count: 1, config: {} },
};

export function CreateTeamDialog({ open, onOpenChange, defaultProgramId }: CreateTeamDialogProps) {
  const { t } = useTranslation();
  const createTeam = useCreateTeam();

  const { values, setField, isPending, handleSubmit } =
    useFormDialog({
      open,
      onOpenChange,
      initialValues: () => ({
        title: '',
      }),
      mutations: [createTeam],
    });

  const onSubmit = useCallback(() => {
    handleSubmit(() => {
      if (!values.title.trim()) return;

      createTeam.mutate(
        {
          title: values.title.trim(),
          role_assembly: DEFAULT_ROLE_ASSEMBLY,
          ...(defaultProgramId ? { program_id: defaultProgramId } : {}),
        },
        {
          onSuccess: () => onOpenChange(false),
        },
      );
    });
  }, [values, defaultProgramId, createTeam, onOpenChange, handleSubmit]);

  const canSubmit = !!values.title.trim() && !isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('teams.createDialog.title')}</DialogTitle>
          <DialogDescription>{t('teams.createDialog.description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Title */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              {t('teams.createDialog.teamTitle')}
            </label>
            <Input
              value={values.title}
              onChange={(e) => setField('title', e.target.value)}
              placeholder={t('teams.createDialog.teamTitlePlaceholder')}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('teams.createDialog.cancel')}
          </Button>
          <Button onClick={onSubmit} disabled={!canSubmit}>
            {isPending
              ? t('teams.createDialog.submitting')
              : t('teams.createDialog.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

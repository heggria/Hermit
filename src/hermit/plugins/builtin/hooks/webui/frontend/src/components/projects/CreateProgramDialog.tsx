// Dialog for creating a new program/project.

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useCreateProgram } from '@/api/hooks';
import { useFormDialog } from '@/hooks/useFormDialog';

const PRIORITIES = ['low', 'normal', 'high'] as const;
type Priority = (typeof PRIORITIES)[number];

interface CreateProgramDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

export function CreateProgramDialog({ open, onOpenChange }: CreateProgramDialogProps) {
  const { t } = useTranslation();

  const createMutation = useCreateProgram();

  const { values, setField, error, setError, isPending, handleSubmit } =
    useFormDialog({
      open,
      onOpenChange,
      initialValues: () => ({
        title: '',
        goal: '',
        description: '',
        priority: 'normal' as Priority,
      }),
      mutations: [createMutation],
    });

  const onSubmit = useCallback(() => {
    handleSubmit(async () => {
      const trimmedTitle = values.title.trim();
      const trimmedGoal = values.goal.trim();

      if (!trimmedTitle || !trimmedGoal) return;

      setError('');

      await createMutation.mutateAsync({
        title: trimmedTitle,
        goal: trimmedGoal,
        description: values.description.trim() || undefined,
        priority: values.priority,
      });
      onOpenChange(false);
    });
  }, [values, createMutation, onOpenChange, handleSubmit, setError]);

  const isDisabled = isPending || !values.title.trim() || !values.goal.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('projects.create')}</DialogTitle>
          <DialogDescription>
            {t('projects.subtitle')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Title */}
          <div className="space-y-1.5">
            <label htmlFor="program-title" className="text-xs font-medium text-foreground">
              {t('projects.title')} *
            </label>
            <Input
              id="program-title"
              value={values.title}
              onChange={(e) => setField('title', e.target.value)}
              placeholder={t('projects.title')}
              disabled={isPending}
            />
          </div>

          {/* Goal */}
          <div className="space-y-1.5">
            <label htmlFor="program-goal" className="text-xs font-medium text-foreground">
              {t('projects.goal')} *
            </label>
            <Input
              id="program-goal"
              value={values.goal}
              onChange={(e) => setField('goal', e.target.value)}
              placeholder={t('projects.goal')}
              disabled={isPending}
            />
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="program-desc" className="text-xs font-medium text-foreground">
              {t('projects.description')}
            </label>
            <Textarea
              id="program-desc"
              value={values.description}
              onChange={(e) => setField('description', e.target.value)}
              placeholder={t('projects.description')}
              disabled={isPending}
              className="min-h-20"
            />
          </div>

          {/* Priority */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">
              {t('projects.priority')}
            </label>
            <Select value={values.priority} onValueChange={(v) => setField('priority', v as Priority)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRIORITIES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p.charAt(0).toUpperCase() + p.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Error */}
          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
              {t('common.error')}: {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            {t('common.cancel')}
          </Button>
          <Button onClick={onSubmit} disabled={isDisabled}>
            {isPending && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
            {t('projects.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

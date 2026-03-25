import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { NumberStepper } from '@/components/ui/number-stepper';
import { FormField } from '@/components/ui/FormField';
import { FormDialog } from '@/components/ui/FormDialog';
import { useCreateSkill, useUpdateSkill } from '@/api/hooks';
import { useFormDialog } from '@/hooks/useFormDialog';
import type { SkillInfo } from '@/types';

interface SkillFormDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly skill?: SkillInfo;
}

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/;

export function SkillFormDialog({
  open,
  onOpenChange,
  skill,
}: SkillFormDialogProps) {
  const { t } = useTranslation();
  const isEditing = !!skill;

  const createMutation = useCreateSkill();
  const updateMutation = useUpdateSkill();

  const { values, setField, error, setError, isPending, handleSubmit } =
    useFormDialog({
      open,
      onOpenChange,
      initialValues: () => ({
        name: skill?.name ?? '',
        description: skill?.description ?? '',
        content: skill?.content ?? '',
        maxTokens: skill?.max_tokens as number | undefined,
      }),
      mutations: [createMutation, updateMutation],
    });

  const onSubmit = useCallback(() => {
    handleSubmit(() => {
      const trimmedName = values.name.trim();
      if (!trimmedName) {
        setError(t('skills.form.nameRequired'));
        return;
      }
      if (!NAME_RE.test(trimmedName)) {
        setError(t('skills.form.nameInvalid'));
        return;
      }

      setError('');

      const payload = {
        name: trimmedName,
        description: values.description.trim(),
        content: values.content,
        max_tokens: values.maxTokens,
      };

      if (isEditing && skill) {
        updateMutation.mutate(payload, {
          onSuccess: () => onOpenChange(false),
          onError: (err) => setError((err as Error).message),
        });
      } else {
        createMutation.mutate(payload, {
          onSuccess: () => onOpenChange(false),
          onError: (err) => setError((err as Error).message),
        });
      }
    });
  }, [
    values,
    isEditing,
    skill,
    createMutation,
    updateMutation,
    onOpenChange,
    handleSubmit,
    setError,
    t,
  ]);

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={
        isEditing ? t('skills.form.editTitle') : t('skills.form.createTitle')
      }
      description={t('skills.subtitle')}
      isPending={isPending}
      error={error || undefined}
      onSubmit={onSubmit}
      submitLabel={
        isEditing ? t('skills.form.updateBtn') : t('skills.form.createBtn')
      }
      pendingLabel={
        isEditing ? t('skills.form.updating') : t('skills.form.creating')
      }
      maxWidth="sm:max-w-lg"
    >
      <FormField label={t('skills.form.name')}>
        <Input
          value={values.name}
          onChange={(e) => setField('name', e.target.value)}
          placeholder={t('skills.form.namePlaceholder')}
          disabled={isEditing}
        />
      </FormField>

      <FormField label={t('skills.form.description')}>
        <Input
          value={values.description}
          onChange={(e) => setField('description', e.target.value)}
          placeholder={t('skills.form.descriptionPlaceholder')}
        />
      </FormField>

      <FormField label={t('skills.form.content')}>
        <Textarea
          value={values.content}
          onChange={(e) => setField('content', e.target.value)}
          placeholder={t('skills.form.contentPlaceholder')}
          rows={8}
          className="font-mono text-xs"
        />
      </FormField>

      <FormField label={t('skills.form.maxTokens')}>
        <NumberStepper
          value={values.maxTokens ?? 0}
          onChange={(v) => setField('maxTokens', v > 0 ? v : undefined)}
          min={0}
          step={500}
        />
      </FormField>
    </FormDialog>
  );
}

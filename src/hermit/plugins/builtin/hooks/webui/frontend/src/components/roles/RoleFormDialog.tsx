import { useState, useCallback, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Trash2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { MultiSelect } from '@/components/ui/multi-select';
import { FormField } from '@/components/ui/FormField';
import { FormDialog } from '@/components/ui/FormDialog';
import { useCreateRole, useUpdateRole, useMcpServers, useSkills } from '@/api/hooks';
import { useFormDialog } from '@/hooks/useFormDialog';
import type { RoleDefinition } from '@/types';

interface RoleFormDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly role?: RoleDefinition;
  readonly onDelete?: (role: RoleDefinition) => void;
  readonly isDeleting?: boolean;
}

export function RoleFormDialog({ open, onOpenChange, role, onDelete, isDeleting }: RoleFormDialogProps) {
  const { t } = useTranslation();
  const isEditing = !!role;
  const isBuiltin = role?.is_builtin ?? false;
  const isReadonly = isBuiltin;

  const [confirmDelete, setConfirmDelete] = useState(false);

  const createMutation = useCreateRole();
  const updateMutation = useUpdateRole();

  const { data: availableMcpServers, isLoading: mcpLoading } = useMcpServers();
  const { data: availableSkills, isLoading: skillsLoading } = useSkills();

  const { values, setField, error, setError, isPending, handleSubmit } =
    useFormDialog({
      open,
      onOpenChange,
      initialValues: () => ({
        name: role?.name ?? '',
        description: role?.description ?? '',
        mcpServers: (role?.mcp_servers ?? []) as readonly string[],
        skills: (role?.skills ?? []) as readonly string[],
      }),
      mutations: [createMutation, updateMutation],
    });

  // Reset confirmDelete when dialog opens (separate from useFormDialog's reset)
  useEffect(() => {
    if (open) {
      setConfirmDelete(false);
    }
  }, [open]);

  const mcpOptions = useMemo(
    () =>
      (availableMcpServers ?? []).map((s) => ({
        value: s.name,
        label: s.name,
        description: s.description || (s.connected ? t('mcpServers.connected') : t('mcpServers.disconnected')),
      })),
    [availableMcpServers, t],
  );

  const skillOptions = useMemo(
    () =>
      (availableSkills ?? []).map((s) => ({
        value: s.name,
        label: s.name,
        description: s.description,
      })),
    [availableSkills],
  );

  const onSubmit = useCallback(() => {
    handleSubmit(() => {
      const trimmedName = values.name.trim();
      if (!trimmedName) {
        setError(t('roles.form.nameRequired'));
        return;
      }

      setError('');

      if (isEditing && role) {
        updateMutation.mutate(
          {
            roleId: role.role_id,
            name: trimmedName,
            description: values.description.trim(),
            mcp_servers: [...values.mcpServers],
            skills: [...values.skills],
          },
          {
            onSuccess: () => onOpenChange(false),
            onError: (err) => setError((err as Error).message),
          },
        );
      } else {
        createMutation.mutate(
          {
            name: trimmedName,
            description: values.description.trim(),
            mcp_servers: [...values.mcpServers],
            skills: [...values.skills],
          },
          {
            onSuccess: () => onOpenChange(false),
            onError: (err) => setError((err as Error).message),
          },
        );
      }
    });
  }, [
    values,
    isEditing,
    role,
    createMutation,
    updateMutation,
    onOpenChange,
    handleSubmit,
    setError,
    t,
  ]);

  const dialogTitle = isBuiltin
    ? t('roles.form.detailTitle')
    : isEditing
      ? t('roles.form.editTitle')
      : t('roles.form.createTitle');

  const formFields = (
    <>
      <FormField label={t('roles.form.name')}>
        <Input
          value={values.name}
          onChange={(e) => setField('name', e.target.value)}
          placeholder={t('roles.form.namePlaceholder')}
          disabled={isReadonly}
        />
      </FormField>

      <FormField label={t('roles.form.description')}>
        <Textarea
          value={values.description}
          onChange={(e) => setField('description', e.target.value)}
          placeholder={t('roles.form.descriptionPlaceholder')}
          rows={3}
          disabled={isReadonly}
        />
      </FormField>

      {!isReadonly && (
        <>
          <FormField label={t('roles.form.mcpServers')}>
            <MultiSelect
              options={mcpOptions}
              selected={values.mcpServers}
              onSelectedChange={(v) => setField('mcpServers', v)}
              placeholder={t('roles.form.mcpServersSelectPlaceholder')}
              customHint={t('roles.form.mcpServersCustomHint')}
              emptyText={t('roles.form.noMcpServersAvailable')}
              isLoading={mcpLoading}
              allowCustom
            />
          </FormField>

          <FormField label={t('roles.form.skills')}>
            <MultiSelect
              options={skillOptions}
              selected={values.skills}
              onSelectedChange={(v) => setField('skills', v)}
              placeholder={t('roles.form.skillsSelectPlaceholder')}
              customHint={t('roles.form.skillsCustomHint')}
              emptyText={t('roles.form.noSkillsAvailable')}
              isLoading={skillsLoading}
              allowCustom
            />
          </FormField>
        </>
      )}

      {isReadonly && (values.mcpServers.length > 0 || values.skills.length > 0) && (
        <>
          {values.mcpServers.length > 0 && (
            <FormField label={t('roles.form.mcpServers')}>
              <p className="text-sm text-muted-foreground">
                {values.mcpServers.join(', ')}
              </p>
            </FormField>
          )}
          {values.skills.length > 0 && (
            <FormField label={t('roles.form.skills')}>
              <p className="text-sm text-muted-foreground">
                {values.skills.join(', ')}
              </p>
            </FormField>
          )}
        </>
      )}
    </>
  );

  // Readonly mode: plain Dialog with just a Close button
  if (isReadonly) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{dialogTitle}</DialogTitle>
            <DialogDescription>{t('roles.subtitle')}</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">{formFields}</div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  // Editable mode: FormDialog with optional delete footer
  const deleteFooter =
    isEditing && onDelete ? (
      <div className="mr-auto">
        {confirmDelete ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-destructive">{t('roles.confirmDeleteInline')}</span>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => { onDelete(role!); }}
              disabled={isDeleting}
            >
              {isDeleting ? t('common.loading') : t('common.delete')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmDelete(false)}
              disabled={isDeleting}
            >
              {t('common.cancel')}
            </Button>
          </div>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="gap-1.5 text-muted-foreground hover:text-destructive"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="size-3.5" />
            {t('common.delete')}
          </Button>
        )}
      </div>
    ) : undefined;

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={dialogTitle}
      description={t('roles.subtitle')}
      isPending={isPending}
      error={error}
      onSubmit={onSubmit}
      submitLabel={isEditing ? t('roles.form.updateBtn') : t('roles.form.createBtn')}
      pendingLabel={isEditing ? t('roles.form.updating') : t('roles.form.creating')}
      footer={deleteFooter}
    >
      {formFields}
    </FormDialog>
  );
}

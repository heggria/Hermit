// Dialog for creating a new team with program selection and role assembly.

import { useState, useCallback, useEffect } from 'react';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Plus, Trash2 } from 'lucide-react';
import { useCreateTeam, useProgramList, useRoleList } from '@/api/hooks';
import { useFormDialog } from '@/hooks/useFormDialog';

interface RoleEntry {
  readonly role: string;
  readonly count: number;
}

interface CreateTeamDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly defaultProgramId?: string;
}

export function CreateTeamDialog({ open, onOpenChange, defaultProgramId }: CreateTeamDialogProps) {
  const { t } = useTranslation();
  const createTeam = useCreateTeam();
  const { data: programsData } = useProgramList(undefined, 100);
  const { data: rolesData } = useRoleList(true, 100);

  const programs = programsData?.programs ?? [];
  const roles = rolesData?.roles ?? [];

  // Role assembly sub-form state (not managed by useFormDialog)
  const [roleEntries, setRoleEntries] = useState<RoleEntry[]>([]);
  const [newRole, setNewRole] = useState('');
  const [newCount, setNewCount] = useState(1);

  const { values, setField, isPending, handleSubmit } =
    useFormDialog({
      open,
      onOpenChange,
      initialValues: () => ({
        programId: defaultProgramId ?? '',
        title: '',
        workspaceId: '',
      }),
      mutations: [createTeam],
    });

  // Reset role assembly sub-form state when dialog opens
  useEffect(() => {
    if (open) {
      setRoleEntries([]);
      setNewRole('');
      setNewCount(1);
    }
  }, [open]);

  const handleAddRole = useCallback(() => {
    if (!newRole) return;
    const exists = roleEntries.some((e) => e.role === newRole);
    if (exists) return;
    setRoleEntries((prev) => [...prev, { role: newRole, count: newCount }]);
    setNewRole('');
    setNewCount(1);
  }, [newRole, newCount, roleEntries]);

  const handleRemoveRole = useCallback((role: string) => {
    setRoleEntries((prev) => prev.filter((e) => e.role !== role));
  }, []);

  const onSubmit = useCallback(() => {
    handleSubmit(() => {
      if (!values.programId || !values.title.trim()) return;

      const roleAssembly: Record<string, { role: string; count: number; config: Record<string, unknown> }> = {};
      for (const entry of roleEntries) {
        roleAssembly[entry.role] = {
          role: entry.role,
          count: entry.count,
          config: {},
        };
      }

      createTeam.mutate(
        {
          program_id: values.programId,
          title: values.title.trim(),
          role_assembly: roleAssembly,
        },
        {
          onSuccess: () => onOpenChange(false),
        },
      );
    });
  }, [values, roleEntries, createTeam, onOpenChange, handleSubmit]);

  const canSubmit = !!values.programId && !!values.title.trim() && !isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('teams.createDialog.title')}</DialogTitle>
          <DialogDescription>{t('teams.createDialog.description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Program select */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              {t('teams.createDialog.program')}
            </label>
            <Select value={values.programId} onValueChange={(v) => setField('programId', v)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t('teams.createDialog.programPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {programs.map((p) => (
                  <SelectItem key={p.program_id} value={p.program_id}>
                    {p.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

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

          {/* Role assembly */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              {t('teams.createDialog.roleAssembly')}
            </label>

            {roleEntries.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {roleEntries.map((entry) => (
                  <Badge
                    key={entry.role}
                    variant="secondary"
                    className="gap-1 pr-1"
                  >
                    {entry.role} &times;{entry.count}
                    <button
                      type="button"
                      onClick={() => handleRemoveRole(entry.role)}
                      className="ml-0.5 rounded-sm p-0.5 hover:bg-muted"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}

            <div className="flex items-center gap-2">
              <Select value={newRole} onValueChange={setNewRole}>
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder={t('teams.createDialog.rolePlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {roles
                    .filter((r) => !roleEntries.some((e) => e.role === r.name))
                    .map((r) => (
                      <SelectItem key={r.role_id} value={r.name}>
                        {r.name}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
              <Input
                type="number"
                min={1}
                max={10}
                value={newCount}
                onChange={(e) => setNewCount(Math.max(1, Number(e.target.value)))}
                className="w-16"
              />
              <Button
                variant="outline"
                size="icon"
                onClick={handleAddRole}
                disabled={!newRole}
              >
                <Plus className="size-4" />
              </Button>
            </div>
          </div>

          {/* Workspace ID */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              {t('teams.createDialog.workspaceId')}
            </label>
            <Input
              value={values.workspaceId}
              onChange={(e) => setField('workspaceId', e.target.value)}
              placeholder={t('teams.createDialog.workspaceIdPlaceholder')}
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

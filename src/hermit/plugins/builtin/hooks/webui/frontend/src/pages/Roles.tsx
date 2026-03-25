import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { CardGridSkeleton } from '@/components/ui/skeletons';
import { DataContainer } from '@/components/ui/DataContainer';
import { useRoleList, useDeleteRole } from '@/api/hooks';
import { RoleCard } from '@/components/roles/RoleCard';
import { RoleFormDialog } from '@/components/roles/RoleFormDialog';
import type { RoleDefinition } from '@/types';

export default function Roles() {
  const { t } = useTranslation();
  const { data, isLoading } = useRoleList();
  const deleteMutation = useDeleteRole();

  const [formOpen, setFormOpen] = useState(false);
  const [selectedRole, setSelectedRole] = useState<RoleDefinition | undefined>(undefined);

  const roles = data?.roles ?? [];
  const customRoles = roles.filter((r) => !r.is_builtin);
  const builtinRoles = roles.filter((r) => r.is_builtin);
  const allRoles = [...customRoles, ...builtinRoles];

  const handleCreate = useCallback(() => {
    setSelectedRole(undefined);
    setFormOpen(true);
  }, []);

  const handleCardClick = useCallback((role: RoleDefinition) => {
    setSelectedRole(role);
    setFormOpen(true);
  }, []);

  const handleDelete = useCallback((role: RoleDefinition) => {
    deleteMutation.mutate(role.role_id, {
      onSuccess: () => setFormOpen(false),
    });
  }, [deleteMutation]);

  return (
    <div className="space-y-4">
      <PageHeader
        title={t('roles.title')}
        subtitle={t('roles.subtitle')}
        action={{ label: t('roles.create'), icon: <Plus className="size-4" />, onClick: handleCreate }}
      />

      {/* Inline hint when no custom roles */}
      {!isLoading && customRoles.length === 0 && builtinRoles.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {t('roles.emptyState')}{' '}
          <button
            type="button"
            onClick={handleCreate}
            className="text-primary hover:underline"
          >
            {t('roles.create')}
          </button>
        </p>
      )}

      <DataContainer
        isLoading={isLoading}
        isEmpty={allRoles.length === 0}
        skeleton={<CardGridSkeleton count={6} />}
        emptyState={null}
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {allRoles.map((role) => (
            <RoleCard
              key={role.role_id}
              role={role}
              onClick={handleCardClick}
            />
          ))}
        </div>
      </DataContainer>

      <RoleFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        role={selectedRole}
        onDelete={handleDelete}
        isDeleting={deleteMutation.isPending}
      />
    </div>
  );
}

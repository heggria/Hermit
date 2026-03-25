import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles, BookOpen, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { ItemActionButtons } from '@/components/ui/ItemActionButtons';
import { PageHeader } from '@/components/layout/PageHeader';
import { CardGridSkeleton } from '@/components/ui/skeletons';
import { EmptyState } from '@/components/layout/EmptyState';
import { DataContainer } from '@/components/ui/DataContainer';
import { DeleteConfirmDialog } from '@/components/ui/DeleteConfirmDialog';
import { useSkills, useDeleteSkill } from '@/api/hooks';
import { SkillFormDialog } from '@/components/skills/SkillFormDialog';
import { SkillDetailDialog } from '@/components/skills/SkillDetailDialog';
import { RecommendedSkills } from '@/components/skills/RecommendedSkills';
import type { SkillInfo } from '@/types';

export default function Skills() {
  const { t } = useTranslation();
  const { data: skills, isLoading } = useSkills();
  const deleteMutation = useDeleteSkill();

  const [formOpen, setFormOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillInfo | undefined>(
    undefined,
  );
  const [deleteTarget, setDeleteTarget] = useState<SkillInfo | null>(null);
  const [detailSkill, setDetailSkill] = useState<SkillInfo | null>(null);

  const userSkills = (skills ?? []).filter((s) => s.source === 'user');
  const builtinSkills = (skills ?? []).filter((s) => s.source !== 'user');
  const allSkills = [...userSkills, ...builtinSkills];
  const installedNames = new Set(allSkills.map((s) => s.name));

  const handleCreate = useCallback(() => {
    setEditingSkill(undefined);
    setFormOpen(true);
  }, []);

  const handleEdit = useCallback((skill: SkillInfo) => {
    setEditingSkill(skill);
    setFormOpen(true);
  }, []);

  const handleDeleteClick = useCallback((skill: SkillInfo) => {
    setDeleteTarget(skill);
  }, []);

  const handleDeleteConfirm = useCallback(() => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.name, {
      onSuccess: () => setDeleteTarget(null),
    });
  }, [deleteTarget, deleteMutation]);

  return (
    <div className="space-y-4">
      <PageHeader
        title={t('skills.title')}
        subtitle={t('skills.subtitle')}
        action={{ label: t('skills.create'), icon: <Plus className="size-4" />, onClick: handleCreate }}
      />

      <DataContainer
        isLoading={isLoading}
        isEmpty={allSkills.length === 0}
        skeleton={<CardGridSkeleton count={6} />}
        emptyState={<EmptyState icon={<Sparkles className="size-10" />} title={t('skills.empty')} />}
      >
        <>
          <p className="text-xs text-muted-foreground">
            {t('skills.totalCount', { count: allSkills.length })}
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {allSkills.map((skill) => (
              <div
                key={skill.name}
                className="cursor-pointer rounded-2xl border border-border bg-card p-4 transition-colors hover:bg-accent/50"
                onClick={() => setDetailSkill(skill)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setDetailSkill(skill);
                  }
                }}
              >
                <div className="flex items-start gap-2.5">
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/10">
                    <BookOpen className="size-3.5 text-amber-600 dark:text-amber-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-1">
                      <h3 className="truncate text-sm font-semibold text-foreground">
                        {skill.name}
                      </h3>
                      <div className="flex items-center gap-0.5 shrink-0">
                        {skill.source !== 'user' && (
                          <Badge
                            variant="secondary"
                            className="text-[10px] px-1.5 py-0"
                          >
                            {t('skills.builtinBadge')}
                          </Badge>
                        )}
                        {skill.source === 'user' && (
                          <ItemActionButtons
                            onEdit={() => handleEdit(skill)}
                            onDelete={() => handleDeleteClick(skill)}
                          />
                        )}
                      </div>
                    </div>
                    {skill.description ? (
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground line-clamp-2">
                        {skill.description}
                      </p>
                    ) : (
                      <p className="mt-1 text-xs italic text-muted-foreground/60">
                        {t('skills.noDescription')}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      </DataContainer>

      {!isLoading && <RecommendedSkills installedNames={installedNames} />}

      <SkillFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        skill={editingSkill}
      />

      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        itemName={deleteTarget?.name ?? ''}
        onConfirm={handleDeleteConfirm}
        isLoading={deleteMutation.isPending}
        title={t('skills.deleteTitle')}
        description={t('skills.deleteConfirm', { name: deleteTarget?.name ?? '' })}
      />

      <SkillDetailDialog
        open={!!detailSkill}
        onOpenChange={(open) => !open && setDetailSkill(null)}
        skill={detailSkill}
      />
    </div>
  );
}

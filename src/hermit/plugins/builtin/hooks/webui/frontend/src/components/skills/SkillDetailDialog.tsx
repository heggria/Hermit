import { useTranslation } from 'react-i18next';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import type { SkillInfo } from '@/types';

interface SkillDetailDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly skill: SkillInfo | null;
}

export function SkillDetailDialog({
  open,
  onOpenChange,
  skill,
}: SkillDetailDialogProps) {
  const { t } = useTranslation();

  if (!skill) return null;

  const isUser = skill.source === 'user';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <DialogTitle>{skill.name}</DialogTitle>
            {!isUser && (
              <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                {t('skills.builtinBadge')}
              </Badge>
            )}
          </div>
          {skill.description && (
            <DialogDescription>{skill.description}</DialogDescription>
          )}
        </DialogHeader>

        {/* Only show raw content for user-defined skills */}
        {isUser && skill.content && (
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground">
              {t('skills.form.content')}
            </label>
            <pre className="max-h-80 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs text-foreground/80">
              {skill.content}
            </pre>
          </div>
        )}

        {skill.max_tokens != null && skill.max_tokens > 0 && (
          <div className="text-xs text-muted-foreground">
            {t('skills.form.maxTokens')}: {skill.max_tokens}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

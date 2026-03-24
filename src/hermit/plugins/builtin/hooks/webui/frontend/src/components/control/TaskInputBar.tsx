// Task submission bar with auto-resize textarea, policy selector, and submit button.

import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Send, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useSubmitTask } from '@/api/hooks';

const POLICY_PROFILES = ['autonomous', 'supervised', 'default'] as const;
type PolicyProfile = (typeof POLICY_PROFILES)[number];

const MAX_ROWS = 3;
const LINE_HEIGHT_PX = 24;

export function TaskInputBar() {
  const { t } = useTranslation();
  const [description, setDescription] = useState('');
  const [policy, setPolicy] = useState<PolicyProfile>('autonomous');
  const [showSuccess, setShowSuccess] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const submitMutation = useSubmitTask();

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = MAX_ROWS * LINE_HEIGHT_PX;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  const handleSubmit = useCallback(async () => {
    const trimmed = description.trim();
    if (!trimmed || submitMutation.isPending) return;

    await submitMutation.mutateAsync({
      description: trimmed,
      policy_profile: policy,
    });

    setDescription('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    setShowSuccess(true);
    setTimeout(() => setShowSuccess(false), 1500);
  }, [description, policy, submitMutation]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setDescription(e.target.value);
      adjustHeight();
    },
    [adjustHeight],
  );

  const isDisabled = submitMutation.isPending || !description.trim();

  return (
    <div
      className={cn(
        'relative rounded-xl border border-border bg-card p-4 ring-1 ring-foreground/5 transition-all',
        showSuccess && 'ring-2 ring-emerald-400/50',
      )}
    >
      {/* Success flash overlay */}
      {showSuccess && (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-emerald-50/80 dark:bg-emerald-900/30 animate-fade-in">
          <span className="text-sm font-medium text-emerald-700 dark:text-emerald-400">
            {t('controlCenter.submitSuccess')}
          </span>
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={description}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={t('controlCenter.inputPlaceholder')}
        disabled={submitMutation.isPending}
        rows={1}
        className={cn(
          'w-full resize-none rounded-xl border border-input bg-transparent px-3 py-2.5',
          'text-sm leading-6 text-foreground placeholder:text-muted-foreground',
          'outline-none transition-colors',
          'focus:border-ring focus:ring-2 focus:ring-ring/30',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'dark:bg-input/20',
        )}
      />

      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">
            {t('controlCenter.policy')}
          </span>
          <Select value={policy} onValueChange={(v) => setPolicy(v as PolicyProfile)}>
            <SelectTrigger size="sm" className="h-7 min-w-[120px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {POLICY_PROFILES.map((p) => (
                <SelectItem key={p} value={p}>
                  {t(`controlCenter.policyOptions.${p}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button
          size="sm"
          disabled={isDisabled}
          onClick={handleSubmit}
          className="gap-1.5"
        >
          {submitMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Send className="size-3.5" />
          )}
          {t('controlCenter.submit')}
        </Button>
      </div>
    </div>
  );
}

// Policy configuration page — profiles, guard chain, action classes, and batch approval.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  usePolicyProfiles,
  usePolicyGuards,
  usePolicyActionClasses,
  useApproveAllMutation,
} from '@/api/hooks';
import type { PolicyProfile } from '@/api/hooks';
import { getVerdictStyle } from '@/lib/status-styles';
import { cn } from '@/lib/utils';
import { PageHeader } from '@/components/layout/PageHeader';
import {
  ShieldCheck,
  Zap,
  Settings2,
  BookOpen,
  FolderLock,
  Terminal,
  Globe,
  Paperclip,
  Map,
  Scale,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ArrowRight,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

// ---------------------------------------------------------------------------
// Profile themes
// ---------------------------------------------------------------------------

interface ProfileTheme {
  readonly icon: LucideIcon;
  readonly accent: string;
  readonly bg: string;
  readonly border: string;
}

const PROFILE_THEMES: Record<string, ProfileTheme> = {
  autonomous: {
    icon: Zap,
    accent: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50/60 dark:bg-emerald-950/30',
    border: 'border-emerald-200/60 dark:border-emerald-800/40',
  },
  supervised: {
    icon: ShieldCheck,
    accent: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50/60 dark:bg-amber-950/30',
    border: 'border-amber-200/60 dark:border-amber-800/40',
  },
  default: {
    icon: Settings2,
    accent: 'text-primary',
    bg: 'bg-primary/5 dark:bg-primary/10',
    border: 'border-primary/20 dark:border-primary/30',
  },
};

const DEFAULT_PROFILE_THEME: ProfileTheme = PROFILE_THEMES.default;

// ---------------------------------------------------------------------------
// Guard themes
// ---------------------------------------------------------------------------

interface GuardTheme {
  readonly icon: LucideIcon;
  readonly color: string;
}

const GUARD_THEMES: Record<string, GuardTheme> = {
  readonly: { icon: BookOpen, color: 'text-sky-600 dark:text-sky-400' },
  filesystem: { icon: FolderLock, color: 'text-violet-600 dark:text-violet-400' },
  shell: { icon: Terminal, color: 'text-orange-600 dark:text-orange-400' },
  network: { icon: Globe, color: 'text-cyan-600 dark:text-cyan-400' },
  attachment: { icon: Paperclip, color: 'text-rose-600 dark:text-rose-400' },
  planning: { icon: Map, color: 'text-indigo-600 dark:text-indigo-400' },
  governance: { icon: Scale, color: 'text-emerald-600 dark:text-emerald-400' },
};

const DEFAULT_GUARD_THEME: GuardTheme = {
  icon: ShieldCheck,
  color: 'text-muted-foreground',
};

// ---------------------------------------------------------------------------
// Risk grouping
// ---------------------------------------------------------------------------

interface RiskGroup {
  readonly level: string;
  readonly icon: LucideIcon;
  readonly accent: string;
  readonly bg: string;
}

const RISK_GROUPS: RiskGroup[] = [
  {
    level: 'low',
    icon: CheckCircle2,
    accent: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50/50 dark:bg-emerald-950/20',
  },
  {
    level: 'medium',
    icon: AlertTriangle,
    accent: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50/50 dark:bg-amber-950/20',
  },
  {
    level: 'high',
    icon: XCircle,
    accent: 'text-red-600 dark:text-red-400',
    bg: 'bg-red-50/50 dark:bg-red-950/20',
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function verdictColor(verdict: string): string {
  return getVerdictStyle(verdict);
}

function verdictLabel(verdict: string): string {
  switch (verdict) {
    case 'allow':
      return 'Allow';
    case 'allow_with_receipt':
      return 'Allow + Receipt';
    case 'approval_required':
      return 'Approval Required';
    default:
      return verdict;
  }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ProfileCard({ profile }: { readonly profile: PolicyProfile }) {
  const theme = PROFILE_THEMES[profile.policy_mode] ?? DEFAULT_PROFILE_THEME;
  const Icon = theme.icon;

  return (
    <div
      className={cn(
        'group relative rounded-2xl border p-5 transition-all duration-200',
        'hover:shadow-md hover:-translate-y-0.5',
        theme.bg,
        theme.border,
      )}
    >
      <div className="flex items-center gap-3">
        <div
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-xl',
            'bg-white/80 dark:bg-white/10 shadow-sm',
          )}
        >
          <Icon className={cn('size-4.5', theme.accent)} />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-sm font-semibold text-foreground">{profile.name}</h4>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground line-clamp-2">
            {profile.description}
          </p>
        </div>
      </div>
    </div>
  );
}

function ProfilesSection() {
  const { t } = useTranslation();
  const { data, isLoading } = usePolicyProfiles();
  const profiles = data?.profiles ?? [];

  return (
    <section>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-foreground">{t('policy.profiles')}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('policy.profilesDesc')}</p>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-2xl bg-muted/50" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {profiles.map((p) => (
            <ProfileCard key={p.name} profile={p} />
          ))}
        </div>
      )}
    </section>
  );
}

function GuardsSection() {
  const { t } = useTranslation();
  const { data, isLoading } = usePolicyGuards();
  const guards = data?.guards ?? [];

  return (
    <section>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-foreground">{t('policy.guards')}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('policy.guardsDesc')}</p>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-xl bg-muted/50" />
          ))}
        </div>
      ) : (
        <div className="relative">
          <div className="space-y-1.5">
            {guards.map((g, idx) => {
              const theme = GUARD_THEMES[g.name] ?? DEFAULT_GUARD_THEME;
              const Icon = theme.icon;
              const isLast = idx === guards.length - 1;

              return (
                <div
                  key={g.name}
                  className={cn(
                    'group relative flex items-center gap-4 rounded-xl px-4 py-3',
                    'bg-card border border-transparent transition-all duration-200',
                    'hover:border-border hover:shadow-sm',
                  )}
                >
                  {/* Order dot on the line */}
                  <div className="relative z-10 flex size-10 shrink-0 items-center justify-center">
                    <div
                      className={cn(
                        'flex size-8 items-center justify-center rounded-lg',
                        'bg-secondary transition-colors group-hover:bg-primary/10',
                      )}
                    >
                      <Icon className={cn('size-4', theme.color)} />
                    </div>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-bold tabular-nums text-muted-foreground/60">
                        {String(g.order).padStart(2, '0')}
                      </span>
                      <p className="text-sm font-medium text-foreground">{g.name}</p>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{g.description}</p>
                  </div>

                  {/* Arrow connector */}
                  {!isLast && (
                    <ArrowRight className="size-3.5 shrink-0 text-muted-foreground/30" />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

function ActionClassesSection() {
  const { t } = useTranslation();
  const { data, isLoading } = usePolicyActionClasses();
  const classes = data?.action_classes ?? [];

  // Group by risk level
  const grouped = RISK_GROUPS.map((rg) => ({
    ...rg,
    items: classes.filter((c) => c.risk === rg.level),
  })).filter((g) => g.items.length > 0);

  return (
    <section>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-foreground">{t('policy.actionClasses')}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('policy.actionClassesDesc')}</p>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-28 animate-pulse rounded-2xl bg-muted/50" />
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          {grouped.map((group) => {
            const GroupIcon = group.icon;
            return (
              <div
                key={group.level}
                className={cn('rounded-2xl border border-border/60 p-4', group.bg)}
              >
                {/* Group header */}
                <div className="mb-3 flex items-center gap-2">
                  <GroupIcon className={cn('size-4', group.accent)} />
                  <h4 className={cn('text-xs font-semibold uppercase tracking-wider', group.accent)}>
                    {t(`common.risk.${group.level}`, group.level)} {t('policy.table.risk')}
                  </h4>
                  <span className="text-[10px] text-muted-foreground">
                    ({group.items.length})
                  </span>
                </div>

                {/* Items grid */}
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {group.items.map((c) => (
                    <div
                      key={c.name}
                      className={cn(
                        'flex items-center justify-between rounded-xl px-3.5 py-2.5',
                        'bg-white/70 dark:bg-white/5',
                        'border border-border/40',
                      )}
                    >
                      <code className="text-xs font-medium text-foreground">{c.name}</code>
                      <span
                        className={cn(
                          'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                          verdictColor(c.default_verdict),
                        )}
                      >
                        {verdictLabel(c.default_verdict)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ApproveAllSection() {
  const { t } = useTranslation();
  const [showConfirm, setShowConfirm] = useState(false);
  const mutation = useApproveAllMutation();

  const handleApproveAll = () => {
    mutation.mutate(undefined, {
      onSuccess: () => {
        setShowConfirm(false);
      },
    });
  };

  return (
    <section>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-foreground">{t('policy.quickActions')}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('policy.quickActionsDesc')}</p>
      </div>

      <div className="rounded-2xl border border-border bg-card p-5">
        {mutation.isSuccess && (
          <div className="mb-4 flex items-center gap-2 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
            <CheckCircle2 className="size-4 shrink-0" />
            {t('policy.approveAllSuccess', {
              count: mutation.data?.approved ?? 0,
              total: mutation.data?.total ?? 0,
            })}
          </div>
        )}

        {mutation.isError && (
          <div className="mb-4 flex items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
            <XCircle className="size-4 shrink-0" />
            {t('common.error')}: {(mutation.error as Error).message}
          </div>
        )}

        {!showConfirm ? (
          <button
            type="button"
            onClick={() => setShowConfirm(true)}
            className={cn(
              'inline-flex items-center gap-2 rounded-xl px-5 py-2.5',
              'bg-primary text-sm font-medium text-primary-foreground shadow-sm',
              'transition-all hover:bg-primary/90 hover:shadow-md',
              'disabled:opacity-50',
            )}
            disabled={mutation.isPending}
          >
            <CheckCircle2 className="size-4" />
            {t('policy.approveAll')}
          </button>
        ) : (
          <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50/80 px-4 py-3 dark:border-amber-800/40 dark:bg-amber-950/40">
            <AlertTriangle className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <p className="flex-1 text-sm text-amber-700 dark:text-amber-300">
              {t('policy.approveAllConfirm')}
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleApproveAll}
                disabled={mutation.isPending}
                className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                {mutation.isPending ? t('common.loading') : t('common.confirm')}
              </button>
              <button
                type="button"
                onClick={() => setShowConfirm(false)}
                disabled={mutation.isPending}
                className="rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-muted-foreground shadow-sm transition-colors hover:bg-muted dark:bg-card disabled:opacity-50"
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Policy() {
  const { t } = useTranslation();

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-8">
      <PageHeader
        title={t('policy.title')}
        subtitle={t('policy.subtitle')}
      />

      <ProfilesSection />
      <GuardsSection />
      <ActionClassesSection />
      <ApproveAllSection />
    </div>
  );
}

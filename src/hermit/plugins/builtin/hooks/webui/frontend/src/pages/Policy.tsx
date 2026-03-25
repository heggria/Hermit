// Policy configuration page — profiles, guard chain, action classes, and batch approval.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  usePolicyProfiles,
  usePolicyGuards,
  usePolicyActionClasses,
  useApproveAllMutation,
} from '@/api/hooks';
import {
  getRiskStyle as getRiskStyleObj,
  getVerdictStyle,
} from '@/lib/status-styles';

// ---------------------------------------------------------------------------
// Risk badge helper
// ---------------------------------------------------------------------------

function riskColor(risk: string): string {
  const s = getRiskStyleObj(risk);
  return `${s.bg} ${s.text}`;
}

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

function ProfilesSection() {
  const { t } = useTranslation();
  const { data, isLoading } = usePolicyProfiles();
  const profiles = data?.profiles ?? [];

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">
        {t('policy.profiles')}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t('policy.profilesDesc')}
      </p>

      {isLoading ? (
        <div className="mt-5 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-5 w-64 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="pb-2 pr-6 text-xs font-medium text-muted-foreground">
                  {t('policy.table.name')}
                </th>
                <th className="pb-2 pr-6 text-xs font-medium text-muted-foreground">
                  {t('policy.table.description')}
                </th>
                <th className="pb-2 text-xs font-medium text-muted-foreground">
                  {t('policy.table.mode')}
                </th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr
                  key={p.name}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="py-3 pr-6 font-medium text-foreground">
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                      {p.name}
                    </code>
                  </td>
                  <td className="py-3 pr-6 text-muted-foreground">
                    {p.description}
                  </td>
                  <td className="py-3">
                    <span className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                      {p.policy_mode}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function GuardsSection() {
  const { t } = useTranslation();
  const { data, isLoading } = usePolicyGuards();
  const guards = data?.guards ?? [];

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">
        {t('policy.guards')}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t('policy.guardsDesc')}
      </p>

      {isLoading ? (
        <div className="mt-5 space-y-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="h-5 w-48 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : (
        <div className="mt-5 space-y-2">
          {guards.map((g) => (
            <div
              key={g.name}
              className="flex items-center gap-4 rounded-xl bg-secondary px-4 py-3"
            >
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xs font-bold tabular-nums text-primary">
                {g.order}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground">
                  {g.name}
                </p>
                <p className="text-xs text-muted-foreground">{g.description}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ActionClassesSection() {
  const { t } = useTranslation();
  const { data, isLoading } = usePolicyActionClasses();
  const classes = data?.action_classes ?? [];

  return (
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">
        {t('policy.actionClasses')}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t('policy.actionClassesDesc')}
      </p>

      {isLoading ? (
        <div className="mt-5 space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-5 w-80 animate-pulse rounded bg-muted" />
          ))}
        </div>
      ) : (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="pb-2 pr-6 text-xs font-medium text-muted-foreground">
                  {t('policy.table.actionClass')}
                </th>
                <th className="pb-2 pr-6 text-xs font-medium text-muted-foreground">
                  {t('policy.table.risk')}
                </th>
                <th className="pb-2 text-xs font-medium text-muted-foreground">
                  {t('policy.table.defaultVerdict')}
                </th>
              </tr>
            </thead>
            <tbody>
              {classes.map((c) => (
                <tr
                  key={c.name}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="py-3 pr-6 font-medium text-foreground">
                    <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                      {c.name}
                    </code>
                  </td>
                  <td className="py-3 pr-6">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${riskColor(c.risk)}`}
                    >
                      {t(`common.risk.${c.risk}`, c.risk)}
                    </span>
                  </td>
                  <td className="py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${verdictColor(c.default_verdict)}`}
                    >
                      {verdictLabel(c.default_verdict)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
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
    <div className="rounded-2xl bg-card border border-border p-6 shadow-sm">
      <h3 className="text-base font-semibold text-foreground">
        {t('policy.quickActions')}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t('policy.quickActionsDesc')}
      </p>

      <div className="mt-5">
        {mutation.isSuccess && (
          <div className="mb-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
            {t('policy.approveAllSuccess', {
              count: mutation.data?.approved ?? 0,
              total: mutation.data?.total ?? 0,
            })}
          </div>
        )}

        {mutation.isError && (
          <div className="mb-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            {t('common.error')}: {(mutation.error as Error).message}
          </div>
        )}

        {!showConfirm ? (
          <button
            type="button"
            onClick={() => setShowConfirm(true)}
            className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
            disabled={mutation.isPending}
          >
            {t('policy.approveAll')}
          </button>
        ) : (
          <div className="flex items-center gap-3 rounded-xl bg-amber-50 px-4 py-3 dark:bg-amber-950">
            <p className="flex-1 text-sm text-amber-700 dark:text-amber-300">
              {t('policy.approveAllConfirm')}
            </p>
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
              className="rounded-lg bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground shadow-sm transition-colors hover:bg-muted disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Policy() {
  const { t } = useTranslation();

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {t('policy.title')}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t('policy.subtitle')}
        </p>
      </div>

      <ProfilesSection />
      <GuardsSection />
      <ActionClassesSection />
      <ApproveAllSection />
    </div>
  );
}

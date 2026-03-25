import { useTranslation } from 'react-i18next';
import {
  Compass,
  Play,
  ShieldCheck,
  Gauge,
  Search,
  Scale,
  FlaskConical,
  FileText,
  Eye,
  UserCog,
  Server,
  Sparkles,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { RoleDefinition } from '@/types';
import type { LucideIcon } from 'lucide-react';

// ---------------------------------------------------------------------------
// Role → icon + accent mapping
// ---------------------------------------------------------------------------

interface RoleTheme {
  icon: LucideIcon;
  accent: string;   // icon bg
  ring: string;     // hover ring
}

const ROLE_THEMES: Record<string, RoleTheme> = {
  planner:      { icon: Compass,      accent: 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-400',       ring: 'hover:ring-blue-200 dark:hover:ring-blue-800' },
  executor:     { icon: Play,         accent: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-400', ring: 'hover:ring-emerald-200 dark:hover:ring-emerald-800' },
  verifier:     { icon: ShieldCheck,  accent: 'bg-violet-100 text-violet-600 dark:bg-violet-900/40 dark:text-violet-400',   ring: 'hover:ring-violet-200 dark:hover:ring-violet-800' },
  benchmarker:  { icon: Gauge,        accent: 'bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400',     ring: 'hover:ring-amber-200 dark:hover:ring-amber-800' },
  researcher:   { icon: Search,       accent: 'bg-cyan-100 text-cyan-600 dark:bg-cyan-900/40 dark:text-cyan-400',       ring: 'hover:ring-cyan-200 dark:hover:ring-cyan-800' },
  reconciler:   { icon: Scale,        accent: 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/40 dark:text-indigo-400',   ring: 'hover:ring-indigo-200 dark:hover:ring-indigo-800' },
  tester:       { icon: FlaskConical, accent: 'bg-rose-100 text-rose-600 dark:bg-rose-900/40 dark:text-rose-400',       ring: 'hover:ring-rose-200 dark:hover:ring-rose-800' },
  spec:         { icon: FileText,     accent: 'bg-teal-100 text-teal-600 dark:bg-teal-900/40 dark:text-teal-400',       ring: 'hover:ring-teal-200 dark:hover:ring-teal-800' },
  reviewer:     { icon: Eye,          accent: 'bg-orange-100 text-orange-600 dark:bg-orange-900/40 dark:text-orange-400',   ring: 'hover:ring-orange-200 dark:hover:ring-orange-800' },
};

const DEFAULT_THEME: RoleTheme = {
  icon: UserCog,
  accent: 'bg-primary/10 text-primary dark:bg-primary/20',
  ring: 'hover:ring-primary/20',
};

function getTheme(name: string): RoleTheme {
  const key = name.toLowerCase();
  return ROLE_THEMES[key] ?? DEFAULT_THEME;
}

// ---------------------------------------------------------------------------
// Capability chip colors (cycle through)
// ---------------------------------------------------------------------------

const CHIP_COLORS = [
  'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
  'bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
  'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface RoleCardProps {
  readonly role: RoleDefinition;
  readonly onClick?: (role: RoleDefinition) => void;
}

export function RoleCard({ role, onClick }: RoleCardProps) {
  const { t } = useTranslation();
  const theme = getTheme(role.name);
  const Icon = theme.icon;

  const serverCount = role.mcp_servers.length;
  const skillCount = role.skills.length;
  const capabilities = [
    ...role.mcp_servers.map((s) => ({ label: s, kind: 'server' as const })),
    ...role.skills.map((s) => ({ label: s, kind: 'skill' as const })),
  ];

  return (
    <div
      className={cn(
        'group cursor-pointer rounded-2xl border border-border bg-card p-4',
        'ring-1 ring-transparent transition-all duration-200',
        'hover:shadow-md hover:-translate-y-0.5',
        theme.ring,
      )}
      onClick={() => onClick?.(role)}
    >
      {/* Header: icon + name + builtin badge */}
      <div className="flex items-start gap-3">
        <div
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-xl',
            theme.accent,
          )}
        >
          <Icon className="size-4" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h3 className="truncate text-sm font-semibold text-foreground">
              {role.name}
            </h3>
            {role.is_builtin && (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-px text-[10px] font-medium text-muted-foreground">
                {t('roles.builtinBadge')}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground line-clamp-2">
            {role.description || t('roles.noDescription')}
          </p>
        </div>
      </div>

      {/* Capability chips */}
      {capabilities.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {capabilities.map((cap, i) => (
            <span
              key={`${cap.kind}-${cap.label}`}
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium',
                CHIP_COLORS[i % CHIP_COLORS.length],
              )}
            >
              {cap.kind === 'server' ? (
                <Server className="size-3 opacity-60" />
              ) : (
                <Sparkles className="size-3 opacity-60" />
              )}
              {cap.label}
            </span>
          ))}
        </div>
      )}

      {/* Footer meta */}
      {(serverCount > 0 || skillCount > 0) && (
        <div className="mt-3 flex items-center gap-3 border-t border-border/50 pt-2 text-[11px] text-muted-foreground">
          {serverCount > 0 && (
            <span className="flex items-center gap-1">
              <Server className="size-3" />
              {serverCount} {serverCount === 1 ? 'server' : 'servers'}
            </span>
          )}
          {skillCount > 0 && (
            <span className="flex items-center gap-1">
              <Sparkles className="size-3" />
              {skillCount} {skillCount === 1 ? 'skill' : 'skills'}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

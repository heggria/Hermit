// Navigation sidebar for Hermit WebUI.

import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  FolderKanban,
  Users,
  UserCog,
  Plug,
  Sparkles,
  Settings,
  Bell,
  Moon,
  Sun,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useApprovals } from '@/api/hooks';
import { Button } from '@/components/ui/button';
import { LanguageSwitcher } from '@/components/layout/LanguageSwitcher';
import { HelpButton } from '@/components/onboarding/HelpButton';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

const Config = lazy(() => import('@/pages/Config'));

// ---------------------------------------------------------------------------
// Hermit logo — uses currentColor so it adapts to theme
// ---------------------------------------------------------------------------

function HermitLogo({ className }: { readonly className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <rect width="32" height="32" rx="7" fill="currentColor" />
      <path
        d="M16 6.5L8 10.5v6c0 5.6 3.4 10.8 8 12.5 4.6-1.7 8-6.9 8-12.5v-6L16 6.5z"
        fill="white"
        fillOpacity={0.93}
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Nav items
// ---------------------------------------------------------------------------

interface NavItem {
  readonly labelKey: string;
  readonly path: string;
  readonly icon: React.ElementType;
  readonly tourId: string;
}

const NAV_ITEMS: readonly NavItem[] = [
  { labelKey: 'nav.projects', path: '/', icon: FolderKanban, tourId: 'sidebar-projects' },
  { labelKey: 'nav.teams', path: '/teams', icon: Users, tourId: 'sidebar-teams' },
  { labelKey: 'nav.roles', path: '/roles', icon: UserCog, tourId: 'sidebar-roles' },
  { labelKey: 'nav.mcpServers', path: '/mcp-servers', icon: Plug, tourId: 'sidebar-mcp' },
  { labelKey: 'nav.skills', path: '/skills', icon: Sparkles, tourId: 'sidebar-skills' },
] as const;

// ---------------------------------------------------------------------------
// Dark mode hook
// ---------------------------------------------------------------------------

function useDarkMode(): readonly [boolean, () => void] {
  const [isDark, setIsDark] = useState(() => {
    if (typeof window === 'undefined') return false;
    const stored = localStorage.getItem('hermit-theme');
    if (stored === 'dark') return true;
    if (stored === 'light') return false;
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (isDark) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
  }, [isDark]);

  const toggle = useCallback(() => {
    setIsDark((prev) => {
      const next = !prev;
      localStorage.setItem('hermit-theme', next ? 'dark' : 'light');
      return next;
    });
  }, []);

  return [isDark, toggle] as const;
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

export function Sidebar() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { data: approvalData } = useApprovals('pending', 100);
  const pendingCount = approvalData?.approvals?.length ?? 0;
  const [isDark, toggleDark] = useDarkMode();
  const [configOpen, setConfigOpen] = useState(false);

  // Determine which nav item is active based on current path
  function isNavActive(itemPath: string): boolean {
    if (itemPath === '/') {
      return pathname === '/' || pathname.startsWith('/projects');
    }
    return pathname === itemPath || pathname.startsWith(itemPath + '/');
  }

  return (
    <aside className="flex h-full w-60 flex-col bg-sidebar" data-tour-id="sidebar">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 px-5">
        <HermitLogo className="size-7 shrink-0 text-primary" />
        <span className="text-[15px] font-semibold tracking-tight text-sidebar-foreground">
          Hermit
        </span>
      </div>

      {/* Approval bell */}
      {pendingCount > 0 && (
        <div className="mx-3 mb-1 flex items-center gap-2 rounded-lg bg-amber-500/8 px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
          <Bell className="size-3.5" />
          <span>{pendingCount} {t('nav.pendingApprovals')}</span>
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 px-3 py-2">
        {NAV_ITEMS.map((item) => {
          const active = isNavActive(item.path);
          return (
            <NavLink
              key={item.path}
              to={item.path}
              data-tour-id={item.tourId}
              className={cn(
                'group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all duration-200',
                active
                  ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                  : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
              )}
            >
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary" />
              )}
              <item.icon
                className={cn(
                  'size-[18px] shrink-0 transition-colors duration-200',
                  active ? 'text-primary' : 'text-muted-foreground group-hover:text-sidebar-foreground',
                )}
              />
              <span className="flex-1">{t(item.labelKey)}</span>
            </NavLink>
          );
        })}
      </nav>

      {/* Footer: settings + help + theme + lang */}
      <div className="border-t border-sidebar-border px-3 py-2.5" data-tour-id="sidebar-settings">
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setConfigOpen(true)}
            className="text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
            aria-label={t('nav.config')}
          >
            <Settings className="size-4" />
          </Button>
          <HelpButton />
          <div className="ml-auto flex items-center gap-1">
            <LanguageSwitcher />
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleDark}
              className="text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
              aria-label={t(isDark ? 'common.switchToLight' : 'common.switchToDark')}
            >
              {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
        </div>
      </div>

      {/* Config dialog */}
      <Dialog open={configOpen} onOpenChange={setConfigOpen}>
        <DialogContent className="sm:max-w-2xl p-0 gap-0 overflow-hidden" showCloseButton>
          <DialogHeader className="px-6 pt-5 pb-4 border-b border-border">
            <DialogTitle>{t('config.title')}</DialogTitle>
          </DialogHeader>
          <Suspense fallback={
            <div className="flex h-[520px] items-center justify-center">
              <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
            </div>
          }>
            <Config />
          </Suspense>
        </DialogContent>
      </Dialog>
    </aside>
  );
}

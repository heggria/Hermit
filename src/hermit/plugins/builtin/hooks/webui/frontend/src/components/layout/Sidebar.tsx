// Navigation sidebar for Hermit WebUI with route links, pending approval badge, and dark mode toggle.

import { useCallback, useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Terminal,
  LayoutDashboard,
  ShieldCheck,
  Shield,
  MessageSquare,
  Brain,
  Radio,
  BarChart3,
  Settings,
  Moon,
  Sun,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { useApprovals } from '@/api/hooks';
import { LanguageSwitcher } from '@/components/layout/LanguageSwitcher';

interface NavItem {
  readonly labelKey: string;
  readonly path: string;
  readonly icon: React.ElementType;
}

const NAV_ITEMS: readonly NavItem[] = [
  { labelKey: 'nav.controlCenter', path: '/', icon: Terminal },
  { labelKey: 'nav.dashboard', path: '/dashboard', icon: LayoutDashboard },
  { labelKey: 'nav.approvals', path: '/approvals', icon: ShieldCheck },
  { labelKey: 'nav.policy', path: '/policy', icon: Shield },
  { labelKey: 'nav.chat', path: '/chat', icon: MessageSquare },
  { labelKey: 'nav.memory', path: '/memory', icon: Brain },
  { labelKey: 'nav.signals', path: '/signals', icon: Radio },
  { labelKey: 'nav.metrics', path: '/metrics', icon: BarChart3 },
  { labelKey: 'nav.config', path: '/config', icon: Settings },
] as const;

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

export function Sidebar() {
  const { t } = useTranslation();
  const { data: approvalData } = useApprovals('pending', 100);
  const pendingCount = approvalData?.approvals?.length ?? 0;
  const [isDark, toggleDark] = useDarkMode();

  return (
    <aside className="flex h-full w-60 flex-col bg-sidebar">
      {/* Logo area */}
      <div className="flex h-16 items-center gap-3 px-5">
        <div className="flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
          <span className="text-sm font-bold tracking-tight">H</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[15px] font-semibold tracking-tight text-sidebar-foreground">
            Hermit
          </span>
          <span className="text-[10px] font-medium text-muted-foreground">
            Agent Kernel
          </span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 px-3 py-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) =>
              cn(
                'group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all duration-200',
                isActive
                  ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                  : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
              )
            }
          >
            {({ isActive }) => (
              <>
                {/* Active indicator -- terracotta left border */}
                {isActive && (
                  <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary animate-fade-in" />
                )}
                <item.icon
                  className={cn(
                    'size-[18px] shrink-0 transition-colors duration-200',
                    isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-sidebar-foreground',
                  )}
                />
                <span className="flex-1">{t(item.labelKey)}</span>
                {item.labelKey === 'nav.approvals' && pendingCount > 0 && (
                  <Badge
                    className="ml-auto h-5 min-w-5 justify-center rounded-full bg-primary px-1.5 text-[10px] font-semibold text-primary-foreground"
                  >
                    {pendingCount}
                  </Badge>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-sidebar-border px-3 py-3">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-medium text-muted-foreground/70">
            {t('common.governedAgentKernel')}
          </p>
          <div className="flex items-center gap-1">
            <LanguageSwitcher />
            <button
              type="button"
              onClick={toggleDark}
              className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors duration-200 hover:bg-sidebar-accent hover:text-sidebar-foreground"
              aria-label={t(isDark ? 'common.switchToLight' : 'common.switchToDark')}
            >
              {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}

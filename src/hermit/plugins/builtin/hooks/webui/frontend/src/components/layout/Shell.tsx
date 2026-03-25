// App shell providing sidebar navigation and main content area.

import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { cn } from '@/lib/utils';

export function Shell() {
  const { pathname } = useLocation();
  // Split-pane and full-height layouts need no padding
  const needsPadding = pathname !== '/' && !pathname.startsWith('/projects/') && !pathname.startsWith('/teams/');

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className={cn(
        'flex-1 border-l border-border/50 min-w-0',
        needsPadding ? 'overflow-auto' : 'overflow-hidden',
      )}>
        <div className={cn('animate-fade-in', needsPadding ? 'p-6' : 'h-full')}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

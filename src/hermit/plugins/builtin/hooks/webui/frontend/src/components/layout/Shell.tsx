// App shell providing sidebar navigation and main content area.

import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { cn } from '@/lib/utils';

const FULL_HEIGHT_ROUTES = ['/chat', '/'];

export function Shell() {
  const { pathname } = useLocation();
  const isFullHeight = FULL_HEIGHT_ROUTES.includes(pathname);

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main
        className={cn(
          'flex-1 border-l border-border/50',
          isFullHeight ? 'flex flex-col overflow-hidden' : 'overflow-auto',
        )}
      >
        <div
          className={cn(
            'animate-fade-in',
            isFullHeight ? 'flex flex-1 flex-col overflow-hidden' : 'p-6',
          )}
        >
          <Outlet />
        </div>
      </main>
    </div>
  );
}

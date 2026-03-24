// Root application component with React Router and TanStack Query providers.

import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Shell } from '@/components/layout/Shell';

const ControlCenter = lazy(() => import('@/pages/ControlCenter'));
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Approvals = lazy(() => import('@/pages/Approvals'));
const Policy = lazy(() => import('@/pages/Policy'));
const Chat = lazy(() => import('@/pages/Chat'));
const Memory = lazy(() => import('@/pages/Memory'));
const Signals = lazy(() => import('@/pages/Signals'));
const Metrics = lazy(() => import('@/pages/Metrics'));
const Config = lazy(() => import('@/pages/Config'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
    },
  },
});

function PageFallback() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center">
      <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route
              index
              element={
                <Suspense fallback={<PageFallback />}>
                  <ControlCenter />
                </Suspense>
              }
            />
            <Route
              path="dashboard"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Dashboard />
                </Suspense>
              }
            />
            <Route
              path="approvals"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Approvals />
                </Suspense>
              }
            />
            <Route
              path="policy"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Policy />
                </Suspense>
              }
            />
            <Route
              path="chat"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Chat />
                </Suspense>
              }
            />
            <Route
              path="memory"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Memory />
                </Suspense>
              }
            />
            <Route
              path="signals"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Signals />
                </Suspense>
              }
            />
            <Route
              path="metrics"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Metrics />
                </Suspense>
              }
            />
            <Route
              path="config"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Config />
                </Suspense>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

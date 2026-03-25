// Root application component with React Router and TanStack Query providers.

import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Shell } from '@/components/layout/Shell';
import { OnboardingProvider } from '@/components/onboarding/OnboardingProvider';
import { WelcomeDialog } from '@/components/onboarding/WelcomeDialog';
import { TourOverlay } from '@/components/onboarding/TourOverlay';

const Projects = lazy(() => import('@/pages/Projects'));
const ProjectDetail = lazy(() => import('@/pages/ProjectDetail'));
const ProjectTasks = lazy(() => import('@/pages/project/ProjectTasks'));
const ProjectMemory = lazy(() => import('@/pages/project/ProjectMemory'));
const ProjectSignals = lazy(() => import('@/pages/project/ProjectSignals'));
const ProjectApprovals = lazy(() => import('@/pages/project/ProjectApprovals'));
const ProjectPolicy = lazy(() => import('@/pages/project/ProjectPolicy'));
const ProjectChat = lazy(() => import('@/pages/project/ProjectChat'));
const Teams = lazy(() => import('@/pages/Teams'));
const TeamDetail = lazy(() => import('@/pages/TeamDetail'));
const Roles = lazy(() => import('@/pages/Roles'));
const McpServers = lazy(() => import('@/pages/McpServers'));
const Skills = lazy(() => import('@/pages/Skills'));
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
        <OnboardingProvider>
          <Routes>
            <Route element={<Shell />}>
            {/* Projects split layout — list always on left, detail on right */}
            <Route
              element={
                <Suspense fallback={<PageFallback />}>
                  <Projects />
                </Suspense>
              }
            >
              <Route index element={null} />
              <Route
                path="projects/:programId"
                element={
                  <Suspense fallback={<PageFallback />}>
                    <ProjectDetail />
                  </Suspense>
                }
              >
                <Route index element={<Navigate to="tasks" replace />} />
                <Route
                  path="tasks"
                  element={
                    <Suspense fallback={<PageFallback />}>
                      <ProjectTasks />
                    </Suspense>
                  }
                />
                <Route
                  path="memory"
                  element={
                    <Suspense fallback={<PageFallback />}>
                      <ProjectMemory />
                    </Suspense>
                  }
                />
                <Route
                  path="signals"
                  element={
                    <Suspense fallback={<PageFallback />}>
                      <ProjectSignals />
                    </Suspense>
                  }
                />
                <Route
                  path="approvals"
                  element={
                    <Suspense fallback={<PageFallback />}>
                      <ProjectApprovals />
                    </Suspense>
                  }
                />
                <Route
                  path="policy"
                  element={
                    <Suspense fallback={<PageFallback />}>
                      <ProjectPolicy />
                    </Suspense>
                  }
                />
                <Route
                  path="chat"
                  element={
                    <Suspense fallback={<PageFallback />}>
                      <ProjectChat />
                    </Suspense>
                  }
                />
              </Route>
            </Route>
            <Route
              path="teams"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Teams />
                </Suspense>
              }
            />
            <Route
              path="teams/:teamId"
              element={
                <Suspense fallback={<PageFallback />}>
                  <TeamDetail />
                </Suspense>
              }
            />
            <Route
              path="roles"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Roles />
                </Suspense>
              }
            />
            <Route
              path="mcp-servers"
              element={
                <Suspense fallback={<PageFallback />}>
                  <McpServers />
                </Suspense>
              }
            />
            <Route
              path="skills"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Skills />
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
          <WelcomeDialog />
          <TourOverlay />
        </OnboardingProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

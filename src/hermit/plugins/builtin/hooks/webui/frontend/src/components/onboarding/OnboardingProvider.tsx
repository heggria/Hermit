// React context providing onboarding state, step management, and localStorage persistence.

import { createContext, useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { TOUR_STEPS, STORAGE_KEY_COMPLETED, STORAGE_KEY_STEP } from './constants';
import type { OnboardingContextValue, OnboardingStatus } from './types';

export const OnboardingContext = createContext<OnboardingContextValue | null>(null);

function isCompleted(): boolean {
  return localStorage.getItem(STORAGE_KEY_COMPLETED) === 'true';
}

export function OnboardingProvider({ children }: { readonly children: React.ReactNode }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [status, setStatus] = useState<OnboardingStatus>('idle');
  const [currentStep, setCurrentStep] = useState(0);
  const [hasCompleted, setHasCompleted] = useState(isCompleted);

  // Auto-trigger welcome dialog on first visit
  useEffect(() => {
    if (isCompleted()) return;
    const timer = setTimeout(() => setStatus('welcome'), 600);
    return () => clearTimeout(timer);
  }, []);

  // Resolve which steps are available (skip project-requiring steps if not on project page)
  const isOnProjectPage = pathname.startsWith('/projects/') && pathname.includes('/tasks');

  const activeSteps = useMemo(() => {
    if (isOnProjectPage) return TOUR_STEPS;
    // Check if user has a saved project
    const lastProject = localStorage.getItem('hermit-last-project');
    if (lastProject) return TOUR_STEPS;
    // No project available — filter out project-requiring steps
    return TOUR_STEPS.filter((s) => !s.requiresProject);
  }, [isOnProjectPage]);

  const totalSteps = activeSteps.length;
  const currentStepDef = status === 'touring' ? (activeSteps[currentStep] ?? null) : null;

  // Navigate to project page for project-requiring steps
  useEffect(() => {
    if (status !== 'touring') return;
    const step = activeSteps[currentStep];
    if (!step?.requiresProject) return;
    if (isOnProjectPage) return;

    const lastProject = localStorage.getItem('hermit-last-project');
    if (lastProject) {
      navigate(`/projects/${lastProject}/tasks`);
    }
  }, [status, currentStep, activeSteps, isOnProjectPage, navigate]);

  // Navigate to home for sidebar steps when on other pages
  useEffect(() => {
    if (status !== 'touring') return;
    const step = activeSteps[currentStep];
    if (!step) return;
    // For sidebar-* targets or create-project-btn, need to be on root or projects page
    if (step.target === 'create-project-btn' && !pathname.startsWith('/')) {
      navigate('/');
    }
  }, [status, currentStep, activeSteps, pathname, navigate]);

  const startTour = useCallback(() => {
    setCurrentStep(0);
    setStatus('touring');
    localStorage.setItem(STORAGE_KEY_STEP, '0');
  }, []);

  const nextStep = useCallback(() => {
    setCurrentStep((prev) => {
      const next = prev + 1;
      if (next >= totalSteps) {
        setStatus('completed');
        setHasCompleted(true);
        localStorage.setItem(STORAGE_KEY_COMPLETED, 'true');
        localStorage.removeItem(STORAGE_KEY_STEP);
        return prev;
      }
      localStorage.setItem(STORAGE_KEY_STEP, String(next));
      return next;
    });
  }, [totalSteps]);

  const prevStep = useCallback(() => {
    setCurrentStep((prev) => {
      const next = Math.max(0, prev - 1);
      localStorage.setItem(STORAGE_KEY_STEP, String(next));
      return next;
    });
  }, []);

  const skipTour = useCallback(() => {
    setStatus('completed');
    setHasCompleted(true);
    localStorage.setItem(STORAGE_KEY_COMPLETED, 'true');
    localStorage.removeItem(STORAGE_KEY_STEP);
  }, []);

  const completeTour = useCallback(() => {
    setStatus('completed');
    setHasCompleted(true);
    localStorage.setItem(STORAGE_KEY_COMPLETED, 'true');
    localStorage.removeItem(STORAGE_KEY_STEP);
  }, []);

  const resetOnboarding = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY_COMPLETED);
    localStorage.removeItem(STORAGE_KEY_STEP);
    setHasCompleted(false);
    setCurrentStep(0);
    setStatus('welcome');
  }, []);

  const value = useMemo<OnboardingContextValue>(
    () => ({
      status,
      currentStep,
      totalSteps,
      currentStepDef,
      startTour,
      nextStep,
      prevStep,
      skipTour,
      completeTour,
      resetOnboarding,
      hasCompleted,
    }),
    [status, currentStep, totalSteps, currentStepDef, startTour, nextStep, prevStep, skipTour, completeTour, resetOnboarding, hasCompleted],
  );

  return (
    <OnboardingContext.Provider value={value}>
      {children}
    </OnboardingContext.Provider>
  );
}

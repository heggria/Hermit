// Tour step definitions for the onboarding guided tour.
// Steps are grouped into phases: Navigation → Project Setup → Task Workflow → Detail & Operations → Other Sections

import type { TourStepDef } from './types';

export const TOUR_STEPS: readonly TourStepDef[] = [
  // ── Phase 1: Navigation Overview ──
  {
    id: 'sidebar',
    target: 'sidebar',
    i18nPrefix: 'onboarding.tour.steps.sidebar',
    placement: 'right',
  },

  // ── Phase 2: Project Setup ──
  {
    id: 'projectList',
    target: 'sidebar-projects',
    i18nPrefix: 'onboarding.tour.steps.projectList',
    placement: 'right',
  },
  {
    id: 'createProject',
    target: 'create-project-btn',
    i18nPrefix: 'onboarding.tour.steps.createProject',
    placement: 'bottom',
  },
  {
    id: 'projectTabs',
    target: 'project-tabs',
    i18nPrefix: 'onboarding.tour.steps.projectTabs',
    placement: 'bottom',
    requiresProject: true,
  },

  // ── Phase 3: Task Submission ──
  {
    id: 'taskInput',
    target: 'task-input',
    i18nPrefix: 'onboarding.tour.steps.taskInput',
    placement: 'bottom',
    requiresProject: true,
  },
  {
    id: 'policySelector',
    target: 'policy-selector',
    i18nPrefix: 'onboarding.tour.steps.policySelector',
    placement: 'top',
    requiresProject: true,
  },
  {
    id: 'taskCards',
    target: 'task-card-area',
    i18nPrefix: 'onboarding.tour.steps.taskCards',
    placement: 'right',
    requiresProject: true,
  },

  // ── Phase 4: Task Detail & Operations ──
  {
    id: 'taskDetailPanel',
    target: 'task-detail-panel',
    i18nPrefix: 'onboarding.tour.steps.taskDetailPanel',
    placement: 'left',
    requiresProject: true,
  },
  {
    id: 'stepTimeline',
    target: 'step-timeline',
    i18nPrefix: 'onboarding.tour.steps.stepTimeline',
    placement: 'left',
    requiresProject: true,
  },
  {
    id: 'drawerChat',
    target: 'drawer-chat',
    i18nPrefix: 'onboarding.tour.steps.drawerChat',
    placement: 'top',
    requiresProject: true,
  },

  // ── Phase 5: Other Sections ──
  {
    id: 'teams',
    target: 'sidebar-teams',
    i18nPrefix: 'onboarding.tour.steps.teams',
    placement: 'right',
  },
  {
    id: 'roles',
    target: 'sidebar-roles',
    i18nPrefix: 'onboarding.tour.steps.roles',
    placement: 'right',
  },
  {
    id: 'skills',
    target: 'sidebar-skills',
    i18nPrefix: 'onboarding.tour.steps.skills',
    placement: 'right',
  },
  {
    id: 'mcpServers',
    target: 'sidebar-mcp',
    i18nPrefix: 'onboarding.tour.steps.mcpServers',
    placement: 'right',
  },
  {
    id: 'settings',
    target: 'sidebar-settings',
    i18nPrefix: 'onboarding.tour.steps.settings',
    placement: 'top',
  },
] as const;

export const STORAGE_KEY_COMPLETED = 'hermit-onboarding-completed';
export const STORAGE_KEY_STEP = 'hermit-onboarding-step';

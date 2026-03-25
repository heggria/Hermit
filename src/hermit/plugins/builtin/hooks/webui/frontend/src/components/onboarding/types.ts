// Type definitions for the onboarding / guided tour system.

export type OnboardingStatus = 'idle' | 'welcome' | 'touring' | 'completed';

export type Placement = 'top' | 'bottom' | 'left' | 'right';

export interface TourStepDef {
  /** Unique step identifier */
  readonly id: string;
  /** data-tour-id value on the target element */
  readonly target: string;
  /** i18n key prefix — title at `${prefix}.title`, description at `${prefix}.description` */
  readonly i18nPrefix: string;
  /** Preferred popover position relative to target */
  readonly placement: Placement;
  /** If true, the step requires being on a project detail page */
  readonly requiresProject?: boolean;
}

export interface OnboardingContextValue {
  readonly status: OnboardingStatus;
  readonly currentStep: number;
  readonly totalSteps: number;
  readonly currentStepDef: TourStepDef | null;
  readonly startTour: () => void;
  readonly nextStep: () => void;
  readonly prevStep: () => void;
  readonly skipTour: () => void;
  readonly completeTour: () => void;
  readonly resetOnboarding: () => void;
  readonly hasCompleted: boolean;
}

// Centralized status → color mapping utilities for the Hermit WebUI.
//
// Every component that renders a status badge, dot, or pill should import from
// here instead of maintaining its own local mapping.  This keeps visual
// consistency across the UI and makes palette changes a single-file edit.

// ---------------------------------------------------------------------------
// 1. Status styles (task / team / milestone / program entity statuses)
// ---------------------------------------------------------------------------

export interface StatusStyle {
  /** Dot / indicator color class (e.g. "bg-emerald-500") */
  readonly dot: string;
  /** Light background pill class */
  readonly bg: string;
  /** Foreground text class */
  readonly text: string;
  /** Whether the dot should pulse (for "in-progress" states) */
  readonly pulse?: boolean;
}

/** Canonical status → style mapping used throughout the UI. */
export const STATUS_STYLES: Record<string, StatusStyle> = {
  // ---- active / running states ----
  active: {
    dot: 'bg-emerald-500',
    bg: 'bg-emerald-50 dark:bg-emerald-950/50',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  running: {
    dot: 'bg-primary',
    bg: 'bg-sky-50 dark:bg-sky-900/30',
    text: 'text-sky-700 dark:text-sky-400',
    pulse: true,
  },
  dispatching: {
    dot: 'bg-primary',
    bg: 'bg-sky-50 dark:bg-sky-900/30',
    text: 'text-sky-700 dark:text-sky-400',
    pulse: true,
  },
  contracting: {
    dot: 'bg-primary',
    bg: 'bg-sky-50 dark:bg-sky-900/30',
    text: 'text-sky-700 dark:text-sky-400',
    pulse: true,
  },
  preflighting: {
    dot: 'bg-primary',
    bg: 'bg-sky-50 dark:bg-sky-900/30',
    text: 'text-sky-700 dark:text-sky-400',
    pulse: true,
  },

  // ---- waiting / blocked states ----
  queued: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-stone-100 dark:bg-stone-800/50',
    text: 'text-stone-600 dark:text-stone-400',
  },
  pending: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-secondary',
    text: 'text-secondary-foreground',
  },
  ready: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-secondary',
    text: 'text-secondary-foreground',
  },
  waiting: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-secondary',
    text: 'text-secondary-foreground',
  },
  paused: {
    dot: 'bg-amber-500',
    bg: 'bg-amber-50 dark:bg-amber-950/50',
    text: 'text-amber-700 dark:text-amber-300',
  },
  blocked: {
    dot: 'bg-amber-500',
    bg: 'bg-amber-50 dark:bg-amber-950/40',
    text: 'text-amber-700 dark:text-amber-300',
  },
  awaiting_approval: {
    dot: 'bg-amber-500',
    bg: 'bg-amber-50 dark:bg-amber-950/40',
    text: 'text-amber-700 dark:text-amber-300',
  },

  // ---- terminal / success states ----
  completed: {
    dot: 'bg-emerald-500',
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  succeeded: {
    dot: 'bg-emerald-500',
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-700 dark:text-emerald-300',
  },

  // ---- terminal / failure states ----
  failed: {
    dot: 'bg-red-500',
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-700 dark:text-red-300',
  },
  cancelled: {
    dot: 'bg-muted-foreground/30',
    bg: 'bg-stone-50 dark:bg-stone-800/30',
    text: 'text-stone-400 dark:text-stone-500',
  },
  skipped: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-muted',
    text: 'text-muted-foreground',
  },
  superseded: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-muted',
    text: 'text-muted-foreground',
  },

  // ---- special ----
  reconciling: {
    dot: 'bg-violet-500',
    bg: 'bg-violet-50 dark:bg-violet-900/30',
    text: 'text-violet-600 dark:text-violet-400',
  },
  archived: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-muted',
    text: 'text-muted-foreground',
  },
  disbanded: {
    dot: 'bg-muted-foreground/40',
    bg: 'bg-muted',
    text: 'text-muted-foreground',
  },
};

const DEFAULT_STATUS_STYLE: StatusStyle = {
  dot: 'bg-muted-foreground/40',
  bg: 'bg-secondary',
  text: 'text-secondary-foreground',
};

/** Look up a status style, returning a sensible neutral default for unknowns. */
export function getStatusStyle(status: string): StatusStyle {
  return STATUS_STYLES[status] ?? DEFAULT_STATUS_STYLE;
}

// ---------------------------------------------------------------------------
// 2. Risk level styles
// ---------------------------------------------------------------------------

export interface RiskStyle {
  readonly bg: string;
  readonly text: string;
}

export const RISK_STYLES: Record<string, RiskStyle> = {
  low: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  medium: {
    bg: 'bg-amber-50 dark:bg-amber-950/40',
    text: 'text-amber-700 dark:text-amber-300',
  },
  high: {
    bg: 'bg-orange-50 dark:bg-orange-950/40',
    text: 'text-orange-700 dark:text-orange-300',
  },
  critical: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-700 dark:text-red-300',
  },
};

const DEFAULT_RISK_STYLE: RiskStyle = {
  bg: 'bg-muted',
  text: 'text-muted-foreground',
};

/** Look up a risk-level style with a neutral fallback. */
export function getRiskStyle(level: string): RiskStyle {
  return RISK_STYLES[level] ?? DEFAULT_RISK_STYLE;
}

// ---------------------------------------------------------------------------
// 3. Result code styles (receipt / execution outcomes)
// ---------------------------------------------------------------------------

export const RESULT_CODE_STYLES: Record<string, RiskStyle> = {
  succeeded: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  success: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  ok: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  failed: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-700 dark:text-red-300',
  },
  failure: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-700 dark:text-red-300',
  },
  error: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-700 dark:text-red-300',
  },
  uncertain: {
    bg: 'bg-amber-50 dark:bg-amber-950/40',
    text: 'text-amber-700 dark:text-amber-300',
  },
  denied: {
    bg: 'bg-muted',
    text: 'text-muted-foreground',
  },
  skipped: {
    bg: 'bg-muted',
    text: 'text-muted-foreground',
  },
};

/** Look up a result-code style with a neutral fallback. */
export function getResultCodeStyle(code: string): RiskStyle {
  return RESULT_CODE_STYLES[code] ?? DEFAULT_RISK_STYLE;
}

// ---------------------------------------------------------------------------
// 4. Verdict styles (policy verdicts)
// ---------------------------------------------------------------------------

export const VERDICT_STYLES: Record<string, string> = {
  allow: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  allow_with_receipt: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
  approval_required: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
};

/** Look up a verdict style with a neutral fallback. */
export function getVerdictStyle(verdict: string): string {
  return VERDICT_STYLES[verdict] ?? 'bg-muted text-muted-foreground';
}

// ---------------------------------------------------------------------------
// 5. Event type styles (ledger event log)
// ---------------------------------------------------------------------------

export const EVENT_TYPE_STYLES: Record<string, RiskStyle> = {
  task_created: {
    bg: 'bg-blue-50 dark:bg-blue-950/40',
    text: 'text-blue-600 dark:text-blue-400',
  },
  task_started: {
    bg: 'bg-blue-50 dark:bg-blue-950/40',
    text: 'text-blue-600 dark:text-blue-400',
  },
  task_completed: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-600 dark:text-emerald-400',
  },
  task_failed: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-600 dark:text-red-400',
  },
  step_started: {
    bg: 'bg-blue-50 dark:bg-blue-950/40',
    text: 'text-blue-600 dark:text-blue-400',
  },
  step_completed: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-600 dark:text-emerald-400',
  },
  step_failed: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-600 dark:text-red-400',
  },
  approval_requested: {
    bg: 'bg-amber-50 dark:bg-amber-950/40',
    text: 'text-amber-600 dark:text-amber-400',
  },
  approval_granted: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40',
    text: 'text-emerald-600 dark:text-emerald-400',
  },
  approval_denied: {
    bg: 'bg-red-50 dark:bg-red-950/40',
    text: 'text-red-600 dark:text-red-400',
  },
  tool_executed: {
    bg: 'bg-violet-50 dark:bg-violet-950/40',
    text: 'text-violet-600 dark:text-violet-400',
  },
  receipt_issued: {
    bg: 'bg-indigo-50 dark:bg-indigo-950/40',
    text: 'text-indigo-600 dark:text-indigo-400',
  },
};

/** Look up an event-type style with a neutral fallback. */
export function getEventTypeStyle(eventType: string): RiskStyle {
  return EVENT_TYPE_STYLES[eventType] ?? DEFAULT_RISK_STYLE;
}

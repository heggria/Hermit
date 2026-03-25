// Full-viewport spotlight overlay with step popover for the guided tour.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { useElementHighlight } from '@/hooks/useElementHighlight';
import { useOnboarding } from './useOnboarding';
import type { Placement } from './types';

const PADDING = 8;
const RADIUS = 12;
const POPOVER_GAP = 12;
const POPOVER_WIDTH = 340;

function buildMaskPath(
  vw: number,
  vh: number,
  rect: { top: number; left: number; width: number; height: number },
): string {
  const x = rect.left - PADDING;
  const y = rect.top - PADDING;
  const w = rect.width + PADDING * 2;
  const h = rect.height + PADDING * 2;
  const r = RADIUS;

  // Outer rect (full viewport) + inner rounded rect (cutout), evenodd creates hole
  return [
    `M0,0 H${vw} V${vh} H0 Z`,
    `M${x + r},${y}`,
    `H${x + w - r}`,
    `Q${x + w},${y} ${x + w},${y + r}`,
    `V${y + h - r}`,
    `Q${x + w},${y + h} ${x + w - r},${y + h}`,
    `H${x + r}`,
    `Q${x},${y + h} ${x},${y + h - r}`,
    `V${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    'Z',
  ].join(' ');
}

function getPopoverPosition(
  rect: { top: number; left: number; width: number; height: number },
  placement: Placement,
  vw: number,
  vh: number,
): { top: number; left: number; actualPlacement: Placement } {
  const padded = {
    top: rect.top - PADDING,
    left: rect.left - PADDING,
    width: rect.width + PADDING * 2,
    height: rect.height + PADDING * 2,
  };

  let top = 0;
  let left = 0;
  let actualPlacement = placement;

  switch (placement) {
    case 'bottom': {
      top = padded.top + padded.height + POPOVER_GAP;
      left = padded.left + padded.width / 2 - POPOVER_WIDTH / 2;
      if (top + 200 > vh) {
        actualPlacement = 'top';
        top = padded.top - POPOVER_GAP - 200;
      }
      break;
    }
    case 'top': {
      top = padded.top - POPOVER_GAP - 180;
      left = padded.left + padded.width / 2 - POPOVER_WIDTH / 2;
      if (top < 0) {
        actualPlacement = 'bottom';
        top = padded.top + padded.height + POPOVER_GAP;
      }
      break;
    }
    case 'right': {
      top = padded.top + padded.height / 2 - 80;
      left = padded.left + padded.width + POPOVER_GAP;
      if (left + POPOVER_WIDTH > vw) {
        actualPlacement = 'left';
        left = padded.left - POPOVER_GAP - POPOVER_WIDTH;
      }
      break;
    }
    case 'left': {
      top = padded.top + padded.height / 2 - 80;
      left = padded.left - POPOVER_GAP - POPOVER_WIDTH;
      if (left < 0) {
        actualPlacement = 'right';
        left = padded.left + padded.width + POPOVER_GAP;
      }
      break;
    }
  }

  // Clamp within viewport
  left = Math.max(12, Math.min(left, vw - POPOVER_WIDTH - 12));
  top = Math.max(12, top);

  return { top, left, actualPlacement };
}

export function TourOverlay() {
  const { t } = useTranslation();
  const {
    status,
    currentStep,
    totalSteps,
    currentStepDef,
    nextStep,
    prevStep,
    skipTour,
  } = useOnboarding();

  const { rect } = useElementHighlight(
    status === 'touring' ? (currentStepDef?.target ?? null) : null,
  );

  const [vw, setVw] = useState(window.innerWidth);
  const [vh, setVh] = useState(window.innerHeight);

  useEffect(() => {
    const onResize = () => {
      setVw(window.innerWidth);
      setVh(window.innerHeight);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Track whether element is genuinely missing vs still loading
  const [waitTicks, setWaitTicks] = useState(0);
  useEffect(() => {
    setWaitTicks(0);
  }, [currentStep]);
  useEffect(() => {
    if (rect || status !== 'touring') return;
    const timer = setTimeout(() => setWaitTicks((t) => t + 1), 300);
    return () => clearTimeout(timer);
  }, [rect, status, waitTicks]);

  if (status !== 'touring' || !currentStepDef) return null;

  const isFirst = currentStep === 0;
  const isLast = currentStep === totalSteps - 1;

  // If element not found after waiting, show step info in a centered card with skip
  if (!rect) {
    // Still waiting for element to appear (first 1s)
    if (waitTicks < 3) {
      return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="rounded-xl bg-card p-6 shadow-lg">
            <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
          </div>
        </div>,
        document.body,
      );
    }

    // Element not found — show step info centered with navigation
    return createPortal(
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={skipTour}>
        <div
          className={cn(
            'rounded-xl border border-border bg-card p-5 shadow-xl max-w-sm',
            'animate-[tourPopoverIn_0.25s_ease-out]',
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <h3 className="text-sm font-semibold text-foreground">
            {t(`${currentStepDef.i18nPrefix}.title`)}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground/70">
            {t('onboarding.tour.elementNotVisible')}
          </p>
          <div className="mt-2 max-h-48 overflow-y-auto text-xs leading-relaxed text-muted-foreground whitespace-pre-line">
            {t(`${currentStepDef.i18nPrefix}.description`)}
          </div>
          <div className="mt-4 flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground/70">
              {t('onboarding.tour.stepOf', { current: currentStep + 1, total: totalSteps })}
            </span>
            <div className="flex items-center gap-1.5">
              {!isFirst && (
                <Button variant="ghost" size="sm" onClick={prevStep} className="h-7 px-2 text-xs">
                  <ChevronLeft className="mr-0.5 size-3.5" />
                  {t('onboarding.tour.previous')}
                </Button>
              )}
              <Button size="sm" onClick={isLast ? skipTour : nextStep} className="h-7 px-3 text-xs">
                {isLast ? t('onboarding.tour.finish') : t('onboarding.tour.next')}
                {!isLast && <ChevronRight className="ml-0.5 size-3.5" />}
              </Button>
            </div>
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  const maskPath = buildMaskPath(vw, vh, rect);
  const { top, left } = getPopoverPosition(
    rect,
    currentStepDef.placement,
    vw,
    vh,
  );

  return createPortal(
    <>
      {/* Backdrop mask with spotlight cutout */}
      <svg
        className="fixed inset-0 z-50 animate-[spotlightIn_0.2s_ease-out]"
        width={vw}
        height={vh}
        onClick={skipTour}
      >
        <path
          d={maskPath}
          fill="black"
          fillOpacity="0.5"
          fillRule="evenodd"
          clipRule="evenodd"
        />
      </svg>

      {/* Step popover */}
      <div
        className={cn(
          'fixed z-[51] rounded-xl border border-border bg-card p-4 shadow-xl',
          'animate-[tourPopoverIn_0.25s_ease-out]',
        )}
        style={{
          top,
          left,
          width: POPOVER_WIDTH,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          type="button"
          onClick={skipTour}
          className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground/60 transition-colors hover:text-foreground"
          aria-label="Close"
        >
          <X className="size-3.5" />
        </button>

        {/* Content */}
        <h3 className="pr-6 text-sm font-semibold text-foreground">
          {t(`${currentStepDef.i18nPrefix}.title`)}
        </h3>
        <div className="mt-1.5 max-h-48 overflow-y-auto text-xs leading-relaxed text-muted-foreground whitespace-pre-line">
          {t(`${currentStepDef.i18nPrefix}.description`)}
        </div>

        {/* Navigation */}
        <div className="mt-4 flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground/70">
            {t('onboarding.tour.stepOf', {
              current: currentStep + 1,
              total: totalSteps,
            })}
          </span>
          <div className="flex items-center gap-1.5">
            {!isFirst && (
              <Button
                variant="ghost"
                size="sm"
                onClick={prevStep}
                className="h-7 px-2 text-xs"
              >
                <ChevronLeft className="mr-0.5 size-3.5" />
                {t('onboarding.tour.previous')}
              </Button>
            )}
            <Button
              size="sm"
              onClick={isLast ? skipTour : nextStep}
              className="h-7 px-3 text-xs"
            >
              {isLast ? t('onboarding.tour.finish') : t('onboarding.tour.next')}
              {!isLast && <ChevronRight className="ml-0.5 size-3.5" />}
            </Button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}

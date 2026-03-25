// Welcome dialog shown on first visit to introduce Hermit.

import { useTranslation } from 'react-i18next';
import { Shield, ListChecks, SlidersHorizontal, FileCheck2 } from 'lucide-react';
import { useOnboarding } from './useOnboarding';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';

const FEATURES = [
  { icon: Shield, key: 'governed' },
  { icon: ListChecks, key: 'taskFirst' },
  { icon: SlidersHorizontal, key: 'policy' },
  { icon: FileCheck2, key: 'evidence' },
] as const;

export function WelcomeDialog() {
  const { t } = useTranslation();
  const { status, startTour, skipTour } = useOnboarding();

  if (status !== 'welcome') return null;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) skipTour(); }}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader className="text-center">
          {/* Logo */}
          <div className="mx-auto mb-3 flex size-14 items-center justify-center rounded-2xl bg-primary/10">
            <svg
              viewBox="0 0 1024 1024"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="size-8 text-primary"
            >
              <rect width="1024" height="1024" rx="228" fill="currentColor" />
              <g transform="translate(-81 -83) scale(1.15)" fill="none">
                <g transform="translate(-70 -42)">
                  <path d="M726 300C799 350 846 433 846 526C846 676 725 798 575 798C437 798 325 686 325 548C325 428 410 328 526 306" stroke="white" strokeWidth="106" strokeLinecap="round" strokeLinejoin="round"/>
                  <path d="M366 676C410 736 481 771 556 771H602" stroke="white" strokeWidth="96" strokeLinecap="round" strokeLinejoin="round"/>
                  <path d="M448 517L415 447" stroke="white" strokeWidth="58" strokeLinecap="round"/>
                  <path d="M525 507L529 433" stroke="white" strokeWidth="58" strokeLinecap="round"/>
                  <circle cx="406" cy="430" r="34" fill="white"/>
                  <circle cx="530" cy="419" r="34" fill="white"/>
                </g>
              </g>
            </svg>
          </div>
          <DialogTitle className="text-lg">
            {t('onboarding.welcome.title')}
          </DialogTitle>
          <DialogDescription className="text-sm">
            {t('onboarding.welcome.subtitle')}
          </DialogDescription>
        </DialogHeader>

        {/* Feature highlights */}
        <div className="mt-2 space-y-3 px-1">
          {FEATURES.map(({ icon: Icon, key }) => (
            <div key={key} className="flex items-start gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/8">
                <Icon className="size-4 text-primary" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">
                  {t(`onboarding.welcome.features.${key}.title`)}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t(`onboarding.welcome.features.${key}.description`)}
                </p>
              </div>
            </div>
          ))}
        </div>

        <DialogFooter className="mt-4 flex gap-2 sm:justify-center">
          <Button variant="outline" onClick={skipTour} className="flex-1 sm:flex-none">
            {t('onboarding.welcome.skipForNow')}
          </Button>
          <Button onClick={startTour} className="flex-1 sm:flex-none">
            {t('onboarding.welcome.startTour')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

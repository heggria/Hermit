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
              viewBox="0 0 32 32"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="size-8 text-primary"
            >
              <rect width="32" height="32" rx="8" fill="currentColor" />
              <path
                d="M16 6C10.477 6 6 10.477 6 16s4.477 10 10 10 10-4.477 10-10S21.523 6 16 6zm0 3a7 7 0 0 1 6.93 6H16.5a.5.5 0 0 0-.5.5v5.45A7.001 7.001 0 0 1 16 9zm3 12.45V15.5a.5.5 0 0 0-.5-.5H22.9A7.003 7.003 0 0 1 19 21.45z"
                fill="white"
                fillOpacity={0.95}
              />
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

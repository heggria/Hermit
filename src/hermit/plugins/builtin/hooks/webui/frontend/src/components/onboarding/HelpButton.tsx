// Persistent help button in the sidebar footer with dropdown for restarting tour.

import { useTranslation } from 'react-i18next';
import { HelpCircle, RotateCcw, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useOnboarding } from './useOnboarding';

export function HelpButton() {
  const { t } = useTranslation();
  const { resetOnboarding } = useOnboarding();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
          aria-label={t('onboarding.help.label')}
        >
          <HelpCircle className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" sideOffset={8}>
        <DropdownMenuItem onClick={resetOnboarding}>
          <RotateCcw className="mr-2 size-3.5" />
          {t('onboarding.help.restartTour')}
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => {
            // Scroll to top of page / navigate to about section via config
            window.dispatchEvent(new CustomEvent('hermit:open-config'));
          }}
        >
          <Info className="mr-2 size-3.5" />
          {t('onboarding.help.aboutHermit')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();

  const isZh = i18n.language.startsWith('zh');

  function toggle() {
    i18n.changeLanguage(isZh ? 'en' : 'zh');
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      className="text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
      aria-label={t(isZh ? 'common.switchLang.en' : 'common.switchLang.zh')}
    >
      {isZh ? 'EN' : '中'}
    </Button>
  );
}

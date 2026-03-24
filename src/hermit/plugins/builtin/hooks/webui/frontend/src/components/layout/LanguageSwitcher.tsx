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
      size="sm"
      onClick={toggle}
      className="text-xs text-sidebar-foreground/70 hover:text-sidebar-foreground"
    >
      {isZh ? t('common.switchLang.en') : t('common.switchLang.zh')}
    </Button>
  );
}

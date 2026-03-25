import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { useUpdateMcpServerEnv } from '@/api/hooks';

interface ApiKeyDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly serverName: string;
  readonly envKey: string;
  readonly tokenUrl?: string | null;
}

export function ApiKeyDialog({
  open,
  onOpenChange,
  serverName,
  envKey,
  tokenUrl,
}: ApiKeyDialogProps) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const updateEnv = useUpdateMcpServerEnv();

  function handleSave() {
    if (!value.trim()) return;
    updateEnv.mutate(
      { name: serverName, key: envKey, value: value.trim() },
      {
        onSuccess: () => {
          setValue('');
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setValue('');
        onOpenChange(v);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {t('mcpServers.apiKeyDialog.title', { name: serverName })}
          </DialogTitle>
          <DialogDescription>
            {t('mcpServers.apiKeyDialog.description')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              {envKey}
            </span>
            <Input
              id="api-key"
              type="password"
              placeholder={t('mcpServers.apiKeyDialog.placeholder')}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSave()}
              autoFocus
            />
          </div>

          {tokenUrl && (
            <a
              href={tokenUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-primary hover:underline"
            >
              <ExternalLink className="size-3" />
              {t('mcpServers.apiKeyDialog.getToken')}
            </a>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              setValue('');
              onOpenChange(false);
            }}
            disabled={updateEnv.isPending}
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSave}
            disabled={!value.trim() || updateEnv.isPending}
          >
            {updateEnv.isPending
              ? t('common.loading')
              : t('mcpServers.apiKeyDialog.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

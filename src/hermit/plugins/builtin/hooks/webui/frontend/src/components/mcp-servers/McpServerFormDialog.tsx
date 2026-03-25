import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { FormDialog } from '@/components/ui/FormDialog';
import { FormField } from '@/components/ui/FormField';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useCreateMcpServer, useUpdateMcpServer } from '@/api/hooks';
import { useFormDialog } from '@/hooks/useFormDialog';
import type { McpServerInfo } from '@/types';

interface McpServerFormDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly server?: McpServerInfo;
}

function parseLines(text: string): string[] {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
}

function parseKeyValueLines(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eqIdx = trimmed.indexOf('=');
    const colonIdx = trimmed.indexOf(':');
    // Support both KEY=value and Key: value
    if (eqIdx > 0 && (colonIdx < 0 || eqIdx < colonIdx)) {
      result[trimmed.slice(0, eqIdx).trim()] = trimmed.slice(eqIdx + 1).trim();
    } else if (colonIdx > 0) {
      result[trimmed.slice(0, colonIdx).trim()] = trimmed.slice(colonIdx + 1).trim();
    }
  }
  return result;
}

function kvToText(obj: Record<string, string> | undefined, sep: string): string {
  if (!obj) return '';
  return Object.entries(obj)
    .map(([k, v]) => `${k}${sep}${v}`)
    .join('\n');
}

export function McpServerFormDialog({
  open,
  onOpenChange,
  server,
}: McpServerFormDialogProps) {
  const { t } = useTranslation();
  const isEditing = !!server;

  const createMutation = useCreateMcpServer();
  const updateMutation = useUpdateMcpServer();

  const { values, setField, error, setError, isPending, handleSubmit } =
    useFormDialog({
      open,
      onOpenChange,
      initialValues: () => ({
        name: server?.name ?? '',
        transport: server?.transport ?? 'stdio',
        command: server?.command ?? '',
        args: server?.args?.join('\n') ?? '',
        env: kvToText(server?.env, '='),
        url: server?.url ?? '',
        headers: kvToText(server?.headers, ': '),
        description: server?.description ?? '',
      }),
      mutations: [createMutation, updateMutation],
    });

  const onSubmit = useCallback(() => {
    handleSubmit(() => {
      const trimmedName = values.name.trim();
      if (!trimmedName) {
        setError(t('mcpServers.form.nameRequired'));
        return;
      }

      if (values.transport === 'stdio' && !values.command.trim()) {
        setError(t('mcpServers.form.commandRequired'));
        return;
      }
      if (values.transport === 'http' && !values.url.trim()) {
        setError(t('mcpServers.form.urlRequired'));
        return;
      }

      setError('');

      const payload: Record<string, unknown> = {
        name: trimmedName,
        transport: values.transport,
        description: values.description.trim() || undefined,
      };

      if (values.transport === 'stdio') {
        payload.command = values.command.trim();
        const parsedArgs = parseLines(values.args);
        if (parsedArgs.length > 0) payload.args = parsedArgs;
        const parsedEnv = parseKeyValueLines(values.env);
        if (Object.keys(parsedEnv).length > 0) payload.env = parsedEnv;
      } else {
        payload.url = values.url.trim();
        const parsedHeaders = parseKeyValueLines(values.headers);
        if (Object.keys(parsedHeaders).length > 0) payload.headers = parsedHeaders;
      }

      if (isEditing && server) {
        const { name: _, ...updatePayload } = payload;
        updateMutation.mutate(
          { name: server.name, ...updatePayload } as Parameters<
            typeof updateMutation.mutate
          >[0],
          {
            onSuccess: () => onOpenChange(false),
            onError: (err) => setError((err as Error).message),
          },
        );
      } else {
        createMutation.mutate(payload as Parameters<typeof createMutation.mutate>[0], {
          onSuccess: () => onOpenChange(false),
          onError: (err) => setError((err as Error).message),
        });
      }
    });
  }, [
    values,
    isEditing,
    server,
    createMutation,
    updateMutation,
    onOpenChange,
    handleSubmit,
    setError,
    t,
  ]);

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={
        isEditing
          ? t('mcpServers.form.editTitle')
          : t('mcpServers.form.createTitle')
      }
      description={t('mcpServers.subtitle')}
      isPending={isPending}
      error={error || undefined}
      onSubmit={onSubmit}
      submitLabel={
        isEditing
          ? t('mcpServers.form.updateBtn')
          : t('mcpServers.form.createBtn')
      }
      pendingLabel={
        isEditing
          ? t('mcpServers.form.updating')
          : t('mcpServers.form.creating')
      }
    >
      <FormField label={t('mcpServers.form.name')}>
        <Input
          value={values.name}
          onChange={(e) => setField('name', e.target.value)}
          placeholder={t('mcpServers.form.namePlaceholder')}
          disabled={isEditing}
        />
      </FormField>

      <FormField label={t('mcpServers.form.transport')}>
        <Select value={values.transport} onValueChange={(v) => setField('transport', v)} disabled={isEditing}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="stdio">
              {t('mcpServers.form.transportStdio')}
            </SelectItem>
            <SelectItem value="http">
              {t('mcpServers.form.transportHttp')}
            </SelectItem>
          </SelectContent>
        </Select>
      </FormField>

      {values.transport === 'stdio' ? (
        <>
          <FormField label={t('mcpServers.form.command')}>
            <Input
              value={values.command}
              onChange={(e) => setField('command', e.target.value)}
              placeholder={t('mcpServers.form.commandPlaceholder')}
            />
          </FormField>
          <FormField label={t('mcpServers.form.args')}>
            <Textarea
              value={values.args}
              onChange={(e) => setField('args', e.target.value)}
              placeholder={t('mcpServers.form.argsPlaceholder')}
              rows={2}
            />
          </FormField>
          <FormField label={t('mcpServers.form.env')}>
            <Textarea
              value={values.env}
              onChange={(e) => setField('env', e.target.value)}
              placeholder={t('mcpServers.form.envPlaceholder')}
              rows={2}
            />
          </FormField>
        </>
      ) : (
        <>
          <FormField label={t('mcpServers.form.url')}>
            <Input
              value={values.url}
              onChange={(e) => setField('url', e.target.value)}
              placeholder={t('mcpServers.form.urlPlaceholder')}
            />
          </FormField>
          <FormField label={t('mcpServers.form.headers')}>
            <Textarea
              value={values.headers}
              onChange={(e) => setField('headers', e.target.value)}
              placeholder={t('mcpServers.form.headersPlaceholder')}
              rows={2}
            />
          </FormField>
        </>
      )}

      <FormField label={t('mcpServers.form.description')}>
        <Input
          value={values.description}
          onChange={(e) => setField('description', e.target.value)}
          placeholder={t('mcpServers.form.descriptionPlaceholder')}
        />
      </FormField>
    </FormDialog>
  );
}

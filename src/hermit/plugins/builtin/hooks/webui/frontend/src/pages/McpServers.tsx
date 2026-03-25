import { useState, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Server, Plug, Wrench, Plus, LogIn, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ItemActionButtons } from '@/components/ui/ItemActionButtons';
import { PageHeader } from '@/components/layout/PageHeader';
import { EmptyState } from '@/components/layout/EmptyState';
import { CardGridSkeleton } from '@/components/ui/skeletons';
import { DataContainer } from '@/components/ui/DataContainer';
import { DeleteConfirmDialog } from '@/components/ui/DeleteConfirmDialog';
import { useMcpServers, useDeleteMcpServer, useStartMcpOAuth, useReloadMcpServers } from '@/api/hooks';
import { McpServerFormDialog } from '@/components/mcp-servers/McpServerFormDialog';
import { RecommendedServers } from '@/components/mcp-servers/RecommendedServers';
import { ApiKeyDialog } from '@/components/mcp-servers/ApiKeyDialog';
import { useQueryClient } from '@tanstack/react-query';
import type { McpServerInfo } from '@/types';

/** Returns true when a server needs auth and isn't connected. */
function needsLogin(s: McpServerInfo): boolean {
  if (s.connected) return false;
  if ((s.has_empty_env_keys?.length ?? 0) > 0) return true;
  if (s.auth_type === 'oauth' && !s.has_oauth_token) return true;
  return false;
}

export default function McpServers() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: servers, isLoading } = useMcpServers();
  const deleteMutation = useDeleteMcpServer();
  const oauthStart = useStartMcpOAuth();
  const reloadMutation = useReloadMcpServers();

  const [formOpen, setFormOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServerInfo | undefined>(
    undefined,
  );
  const [deleteTarget, setDeleteTarget] = useState<McpServerInfo | null>(null);
  const [apiKeyTarget, setApiKeyTarget] = useState<McpServerInfo | null>(null);

  const userServers = (servers ?? []).filter((s) => s.source === 'user');
  const builtinServers = (servers ?? []).filter((s) => s.source !== 'user');
  const allServers = [...userServers, ...builtinServers];
  const installedNames = new Set(allServers.map((s) => s.name));

  // Listen for OAuth completion messages from popup window
  useEffect(() => {
    function handleMessage(e: MessageEvent) {
      if (e.data?.type === 'mcp-oauth-complete') {
        queryClient.invalidateQueries({ queryKey: ['config', 'mcp-servers'] });
      }
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [queryClient]);

  const handleCreate = useCallback(() => {
    setEditingServer(undefined);
    setFormOpen(true);
  }, []);

  const handleEdit = useCallback((server: McpServerInfo) => {
    setEditingServer(server);
    setFormOpen(true);
  }, []);

  const handleDeleteClick = useCallback((server: McpServerInfo) => {
    setDeleteTarget(server);
  }, []);

  const handleDeleteConfirm = useCallback(() => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.name, {
      onSuccess: () => setDeleteTarget(null),
    });
  }, [deleteTarget, deleteMutation]);

  const handleLogin = useCallback(
    (server: McpServerInfo) => {
      // OAuth flow takes priority for HTTP servers
      if (server.auth_type === 'oauth' && !server.has_oauth_token) {
        oauthStart.mutate(
          { name: server.name, server_url: server.url ?? undefined },
          {
            onSuccess: (data) => {
              window.open(data.auth_url, '_blank', 'width=600,height=700');
            },
          },
        );
        return;
      }
      // API key flow: show dialog to enter token
      if (
        server.auth_type === 'api_key' ||
        (server.has_empty_env_keys?.length ?? 0) > 0
      ) {
        setApiKeyTarget(server);
        return;
      }
    },
    [oauthStart],
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title={t('mcpServers.title')}
        subtitle={t('mcpServers.subtitle')}
        action={{
          label: t('mcpServers.create'),
          icon: <Plus className="size-4" />,
          onClick: handleCreate,
        }}
        extra={
          <Button
            variant="outline"
            size="sm"
            onClick={() => reloadMutation.mutate()}
            disabled={reloadMutation.isPending}
          >
            <RefreshCw className={`size-4 ${reloadMutation.isPending ? 'animate-spin' : ''}`} />
            {t('mcpServers.reload')}
          </Button>
        }
      />

      <DataContainer
        isLoading={isLoading}
        isEmpty={allServers.length === 0}
        skeleton={<CardGridSkeleton count={3} height="h-40" columns="sm:grid-cols-2 lg:grid-cols-3" />}
        emptyState={
          <EmptyState
            icon={<Server className="size-10" />}
            title={t('mcpServers.empty')}
          />
        }
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {allServers.map((server) => {
            const showLogin = needsLogin(server);
            return (
              <div
                key={server.name}
                className="rounded-2xl border border-border bg-card p-5 transition-shadow hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <div className="flex size-9 items-center justify-center rounded-xl bg-primary/10">
                      <Plug className="size-4 text-primary" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-foreground">
                        {server.name}
                      </h3>
                      <p className="text-[11px] text-muted-foreground">
                        {server.transport}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    {server.source !== 'user' && (
                      <Badge
                        variant="secondary"
                        className="shrink-0 text-[10px] px-1.5 py-0"
                      >
                        {t('mcpServers.builtinBadge')}
                      </Badge>
                    )}
                    {showLogin ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-6 gap-1 px-2 text-[10px]"
                        onClick={() => handleLogin(server)}
                      >
                        <LogIn className="size-3" />
                        {t('mcpServers.login')}
                      </Button>
                    ) : (
                      <Badge
                        variant={server.connected ? 'default' : 'secondary'}
                        className="shrink-0 text-[10px]"
                      >
                        {server.connected
                          ? t('mcpServers.connected')
                          : t('mcpServers.disconnected')}
                      </Badge>
                    )}
                    {server.source === 'user' && (
                      <ItemActionButtons
                        onEdit={() => handleEdit(server)}
                        onDelete={() => handleDeleteClick(server)}
                        className="flex items-center gap-0.5 ml-1"
                      />
                    )}
                  </div>
                </div>

                {server.description && (
                  <p className="mt-3 text-xs leading-relaxed text-muted-foreground line-clamp-2">
                    {server.description}
                  </p>
                )}

                {server.tools.length > 0 && (
                  <div className="mt-3 border-t border-border pt-3">
                    <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                      <Wrench className="size-3" />
                      <span>
                        {t('mcpServers.toolCount', { count: server.tools.length })}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {server.tools.slice(0, 8).map((tool) => (
                        <Badge
                          key={tool.name}
                          variant="outline"
                          className="text-[10px] font-normal"
                          title={tool.description}
                        >
                          {tool.name}
                        </Badge>
                      ))}
                      {server.tools.length > 8 && (
                        <Badge
                          variant="outline"
                          className="text-[10px] font-normal text-muted-foreground"
                        >
                          +{server.tools.length - 8}
                        </Badge>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </DataContainer>

      {!isLoading && <RecommendedServers installedNames={installedNames} />}

      <McpServerFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        server={editingServer}
      />

      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        itemName={deleteTarget?.name ?? ''}
        onConfirm={handleDeleteConfirm}
        isLoading={deleteMutation.isPending}
        title={t('mcpServers.deleteTitle')}
        description={t('mcpServers.deleteConfirm', { name: deleteTarget?.name ?? '' })}
      />

      <ApiKeyDialog
        open={!!apiKeyTarget}
        onOpenChange={(open) => !open && setApiKeyTarget(null)}
        serverName={apiKeyTarget?.name ?? ''}
        envKey={
          apiKeyTarget?.auth_env_key ??
          apiKeyTarget?.has_empty_env_keys?.[0] ??
          ''
        }
        tokenUrl={apiKeyTarget?.auth_token_url}
      />
    </div>
  );
}

// Team detail page with DAG editor and right panel (milestones / node detail).

import { useCallback, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ArrowLeft, Archive, Milestone, Pencil, Check, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { getStatusStyle } from '@/lib/status-styles';
import { useTeam, useUpdateTeam } from '@/api/hooks';
import { DagEditor } from '@/components/teams/DagEditor';
import { MilestoneList } from '@/components/teams/MilestoneList';
import { NodeDetailPanel } from '@/components/teams/NodeDetailPanel';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { DeleteConfirmDialog } from '@/components/ui/DeleteConfirmDialog';
import type { RoleSlotSpec } from '@/types';
import type { RoleNodeData } from '@/components/teams/RoleNode';

export default function TeamDetail() {
  const { teamId } = useParams<{ teamId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data, isLoading } = useTeam(teamId ?? '');
  const updateTeam = useUpdateTeam();

  const team = data?.team;
  const milestones = data?.milestones ?? [];

  // Track which node is selected and its data for the right panel
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedNodeData, setSelectedNodeData] = useState<RoleNodeData | null>(null);
  const [milestonesOpen, setMilestonesOpen] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);
  const titleInputRef = useRef<HTMLInputElement>(null);

  // Keep a ref to the latest node data map so we can look up data on select
  const nodeDataRef = useRef<Record<string, RoleNodeData>>({});

  const handleStatusChange = useCallback(
    (nextStatus: string) => {
      if (!teamId) return;
      updateTeam.mutate({ teamId, status: nextStatus });
    },
    [teamId, updateTeam],
  );

  const handleTitleSave = useCallback(() => {
    const trimmed = titleInputRef.current?.value.trim() ?? '';
    if (!teamId || !trimmed || trimmed === team?.title) {
      setEditingTitle(false);
      return;
    }
    updateTeam.mutate({ teamId, title: trimmed }, {
      onSuccess: () => setEditingTitle(false),
      onError: () => setEditingTitle(false),
    });
  }, [teamId, team?.title, updateTeam]);

  const handleDagSave = useCallback(
    (
      roleAssembly: Record<string, RoleSlotSpec>,
      edges: Array<{ source: string; target: string }>,
      positions: Record<string, { x: number; y: number }>,
    ) => {
      if (!teamId) return;

      // Update our local node data ref
      for (const [key, slot] of Object.entries(roleAssembly)) {
        nodeDataRef.current[key] = {
          role: slot.role,
          count: slot.count,
          description: nodeDataRef.current[key]?.description ?? '',
          config: slot.config ?? {},
        };
      }

      updateTeam.mutate({
        teamId,
        role_assembly: roleAssembly as unknown as Record<string, unknown>,
        metadata: {
          role_graph_edges: edges,
          role_node_positions: positions,
        },
      });
    },
    [teamId, updateTeam],
  );

  const handleNodeSelect = useCallback(
    (nodeId: string | null, nodeData?: RoleNodeData) => {
      setSelectedNodeId(nodeId);
      if (nodeId) {
        // Prefer data passed directly from the DagEditor (always up-to-date)
        if (nodeData) {
          setSelectedNodeData(nodeData);
          nodeDataRef.current[nodeId] = nodeData;
        } else {
          const fromRef = nodeDataRef.current[nodeId];
          const fromTeam = team?.role_assembly?.[nodeId];
          if (fromRef) {
            setSelectedNodeData(fromRef);
          } else if (fromTeam) {
            setSelectedNodeData({
              role: fromTeam.role,
              count: fromTeam.count,
              description: '',
              config: fromTeam.config ?? {},
            });
          }
        }
      } else {
        setSelectedNodeData(null);
      }
    },
    [team],
  );

  const handleNodeDataChange = useCallback(
    (nodeId: string, patch: Partial<RoleNodeData>) => {
      // Update our ref
      const existing = nodeDataRef.current[nodeId];
      if (existing) {
        nodeDataRef.current[nodeId] = { ...existing, ...patch };
      }
      // Update the panel if this is the selected node
      if (nodeId === selectedNodeId) {
        setSelectedNodeData((prev) => (prev ? { ...prev, ...patch } : prev));
      }
    },
    [selectedNodeId],
  );

  const handleNodeUpdate = useCallback(
    (nodeId: string, patch: Partial<RoleNodeData>) => {
      // Update the DagEditor's node data via the exposed updater
      const updater = (window as unknown as Record<string, unknown>).__dagUpdateNode as
        | ((id: string, p: Partial<RoleNodeData>) => void)
        | undefined;
      if (updater) {
        updater(nodeId, patch);
      }
    },
    [],
  );

  const handleNodeDelete = useCallback(
    (nodeId: string) => {
      // Trigger delete key event equivalent — just deselect and let the DagEditor handle it
      setSelectedNodeId(null);
      setSelectedNodeData(null);
      delete nodeDataRef.current[nodeId];

      // Directly remove via DagEditor's window-exposed updater approach
      // For simplicity, we trigger a programmatic Backspace event
      // Actually, let's use a simpler approach: expose a delete function
      const updater = (window as unknown as Record<string, unknown>).__dagUpdateNode as
        | ((id: string, p: Partial<RoleNodeData>) => void)
        | undefined;
      if (updater) {
        // Mark as deleted — DagEditor will filter it out on next save
        updater(nodeId, { count: -1 });
      }
    },
    [],
  );

  if (isLoading || !team) {
    return (
      <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 space-y-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-[600px] animate-pulse rounded-xl bg-muted" />
      </div>
    );
  }

  // Build node data ref from team data on first render
  if (Object.keys(nodeDataRef.current).length === 0 && team.role_assembly) {
    for (const [key, slot] of Object.entries(team.role_assembly)) {
      nodeDataRef.current[key] = {
        role: slot.role,
        count: slot.count,
        description: '',
        config: slot.config ?? {},
      };
    }
  }

  const isArchived = team.status === 'archived';

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 px-4 py-4 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => navigate('/teams')}
          >
            <ArrowLeft className="size-4" />
          </Button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {editingTitle ? (
                <form
                  className="flex items-center gap-1.5"
                  onSubmit={(e) => { e.preventDefault(); handleTitleSave(); }}
                >
                  <Input
                    ref={titleInputRef}
                    autoFocus
                    defaultValue={team.title}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') setEditingTitle(false);
                    }}
                    className="h-7 text-lg font-semibold w-48"
                  />
                  <Button type="submit" variant="ghost" size="icon-sm">
                    <Check className="size-3.5" />
                  </Button>
                  <Button type="button" variant="ghost" size="icon-sm" onClick={() => setEditingTitle(false)}>
                    <X className="size-3.5" />
                  </Button>
                </form>
              ) : (
                <h1
                  className="group/title flex items-center gap-1.5 text-lg font-semibold tracking-tight text-foreground truncate cursor-pointer"
                  onClick={() => {
                    setEditingTitle(true);
                  }}
                >
                  {team.title}
                  <Pencil className="size-3 text-muted-foreground/0 group-hover/title:text-muted-foreground/60 transition-colors" />
                </h1>
              )}
              <span
                className={cn(
                  'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium shrink-0',
                  getStatusStyle(team.status).bg,
                  getStatusStyle(team.status).text,
                )}
              >
                {team.status}
              </span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs"
            onClick={() => setMilestonesOpen(true)}
          >
            <Milestone className="size-3.5" />
            {t('teams.milestones')}
            {milestones.length > 0 && (
              <span className="ml-0.5 tabular-nums text-muted-foreground">
                {milestones.length}
              </span>
            )}
          </Button>
          {!isArchived && (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 text-xs"
              onClick={() => setArchiveConfirmOpen(true)}
              disabled={updateTeam.isPending}
            >
              <Archive className="size-3.5" />
              {t('teams.detail.archive')}
            </Button>
          )}
        </div>
      </div>

      {/* Main area: DAG editor + optional node detail */}
      <div className="flex flex-1 min-h-0 overflow-hidden border-t border-border/50">
        {/* DAG editor */}
        <div className={cn('min-w-0', selectedNodeId ? 'flex-1 border-r border-border/40' : 'w-full')}>
          <DagEditor
            team={team}
            onSave={handleDagSave}
            isSaving={updateTeam.isPending}
            selectedNodeId={selectedNodeId}
            onNodeSelect={handleNodeSelect}
            onNodeDataChange={handleNodeDataChange}
          />
        </div>

        {/* Right panel: node detail only when selected */}
        {selectedNodeId && selectedNodeData && (
          <div className="w-[300px] shrink-0 bg-card overflow-hidden">
            <NodeDetailPanel
              nodeId={selectedNodeId}
              data={selectedNodeData}
              onUpdate={handleNodeUpdate}
              onDelete={handleNodeDelete}
              onClose={() => {
                setSelectedNodeId(null);
                setSelectedNodeData(null);
              }}
            />
          </div>
        )}
      </div>

      {/* Milestones sheet */}
      <Sheet open={milestonesOpen} onOpenChange={setMilestonesOpen}>
        <SheetContent side="right" className="w-full sm:max-w-sm overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{t('teams.detail.milestones')}</SheetTitle>
          </SheetHeader>
          <div className="py-4">
            <MilestoneList teamId={team.team_id} milestones={milestones} />
          </div>
        </SheetContent>
      </Sheet>

      {/* Archive confirmation dialog */}
      <DeleteConfirmDialog
        open={archiveConfirmOpen}
        onOpenChange={setArchiveConfirmOpen}
        itemName={team.title}
        title={t('teams.detail.archiveConfirmTitle')}
        description={t('teams.detail.archiveConfirmDesc', { name: team.title })}
        confirmLabel={t('teams.detail.archive')}
        onConfirm={() => {
          handleStatusChange('archived');
          setArchiveConfirmOpen(false);
        }}
        isLoading={updateTeam.isPending}
      />
    </div>
  );
}

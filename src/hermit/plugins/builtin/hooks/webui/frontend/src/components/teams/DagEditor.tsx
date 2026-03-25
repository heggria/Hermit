// DAG editor using @xyflow/react for visualizing and editing team role graphs.

import { useCallback, useMemo, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  useReactFlow,
  ReactFlowProvider,
  type Node,
  type Edge,
  type Connection,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { Button } from '@/components/ui/button';
import { Save, Check, Plus, Maximize2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { useRoleList } from '@/api/hooks';
import { RoleNode, type RoleNodeData } from './RoleNode';
import type { TeamRecord, RoleSlotSpec } from '@/types';

const nodeTypes = { roleNode: RoleNode };

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function buildNodesFromAssembly(
  roleAssembly: Record<string, RoleSlotSpec>,
  existingPositions: Record<string, { x: number; y: number }>,
  roleDescriptions: Record<string, string>,
): Node[] {
  const entries = Object.entries(roleAssembly);
  const cols = Math.max(Math.ceil(Math.sqrt(entries.length)), 1);

  return entries.map(([key, slot], index) => {
    const saved = existingPositions[key];
    const col = index % cols;
    const row = Math.floor(index / cols);

    return {
      id: key,
      type: 'roleNode',
      position: saved ?? { x: col * 240, y: row * 140 },
      data: {
        role: slot.role,
        count: slot.count,
        description: roleDescriptions[slot.role] ?? '',
        config: slot.config ?? {},
      } satisfies RoleNodeData,
    };
  });
}

function buildEdgesFromMetadata(
  metadata: Record<string, unknown>,
): Edge[] {
  const raw = metadata?.role_graph_edges;
  if (!Array.isArray(raw)) return [];

  return raw.map(
    (e: { source: string; target: string; id?: string }, i: number) => ({
      id: e.id ?? `edge-${i}`,
      source: e.source,
      target: e.target,
      animated: true,
      style: { stroke: 'var(--color-primary)', strokeWidth: 2 },
      interactionWidth: 20,
    }),
  );
}

// ---------------------------------------------------------------------------
// Inner component (needs ReactFlowProvider ancestor)
// ---------------------------------------------------------------------------

interface DagEditorInnerProps {
  readonly team: TeamRecord;
  readonly onSave: (
    roleAssembly: Record<string, RoleSlotSpec>,
    edges: Array<{ source: string; target: string }>,
    positions: Record<string, { x: number; y: number }>,
  ) => void;
  readonly isSaving?: boolean;
  readonly selectedNodeId: string | null;
  readonly onNodeSelect: (nodeId: string | null, data?: RoleNodeData) => void;
  readonly onNodeDataChange: (nodeId: string, data: Partial<RoleNodeData>) => void;
}

function DagEditorInner({
  team,
  onSave,
  isSaving = false,
  selectedNodeId: _selectedNodeId,
  onNodeSelect,
  onNodeDataChange,
}: DagEditorInnerProps) {
  const { t } = useTranslation();
  const { fitView } = useReactFlow();
  const { data: rolesData } = useRoleList(true, 100);
  const availableRoles = rolesData?.roles ?? [];

  const roleDescriptions = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of availableRoles) {
      map[r.name] = r.description;
    }
    return map;
  }, [availableRoles]);

  const savedPositions = useMemo(() => {
    const pos = team.metadata?.role_node_positions;
    if (pos && typeof pos === 'object') return pos as Record<string, { x: number; y: number }>;
    return {};
  }, [team.metadata]);

  const initialNodes = useMemo(
    () => buildNodesFromAssembly(team.role_assembly ?? {}, savedPositions, roleDescriptions),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [team.team_id],
  );
  const initialEdges = useMemo(
    () => buildEdgesFromMetadata(team.metadata ?? {}),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [team.team_id],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [saveFlash, setSaveFlash] = useState(false);

  useEffect(() => {
    setNodes(buildNodesFromAssembly(team.role_assembly ?? {}, savedPositions, roleDescriptions));
    setEdges(buildEdgesFromMetadata(team.metadata ?? {}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.team_id]);

  const updateNodeData = useCallback(
    (nodeId: string, patch: Partial<RoleNodeData>) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId
            ? { ...n, data: { ...(n.data as Record<string, unknown>), ...patch } }
            : n,
        ),
      );
      onNodeDataChange(nodeId, patch);
    },
    [setNodes, onNodeDataChange],
  );

  useEffect(() => {
    (window as unknown as Record<string, unknown>).__dagUpdateNode = updateNodeData;
    return () => {
      delete (window as unknown as Record<string, unknown>).__dagUpdateNode;
    };
  }, [updateNodeData]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            animated: true,
            style: { stroke: 'var(--color-primary)', strokeWidth: 2 },
            interactionWidth: 20,
          },
          eds,
        ),
      );
    },
    [setEdges],
  );

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      onNodeSelect(node.id, node.data as RoleNodeData);
    },
    [onNodeSelect],
  );

  const onPaneClick = useCallback(() => {
    onNodeSelect(null);
  }, [onNodeSelect]);

  const onNodesDelete = useCallback(() => {
    onNodeSelect(null);
  }, [onNodeSelect]);

  // Count how many times each role is already on the canvas
  const roleUsageCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const node of nodes) {
      const d = node.data as unknown as RoleNodeData;
      counts[d.role] = (counts[d.role] ?? 0) + 1;
    }
    return counts;
  }, [nodes]);

  const handleAddRole = useCallback(
    (roleName: string) => {
      let nodeId = roleName;
      const existingIds = new Set(nodes.map((n) => n.id));
      if (existingIds.has(nodeId)) {
        let counter = 2;
        while (existingIds.has(`${roleName}_${counter}`)) counter++;
        nodeId = `${roleName}_${counter}`;
      }

      // Place near center with some offset
      const cols = Math.max(Math.ceil(Math.sqrt(nodes.length + 1)), 1);
      const idx = nodes.length;
      const col = idx % cols;
      const row = Math.floor(idx / cols);

      const newNode: Node = {
        id: nodeId,
        type: 'roleNode',
        position: { x: col * 240, y: row * 140 },
        data: {
          role: roleName,
          count: 1,
          description: roleDescriptions[roleName] ?? '',
          config: {},
        } satisfies RoleNodeData,
      };

      setNodes((nds) => [...nds, newNode]);
    },
    [nodes, roleDescriptions, setNodes],
  );

  const handleSave = useCallback(() => {
    const roleAssembly: Record<string, RoleSlotSpec> = {};
    const positions: Record<string, { x: number; y: number }> = {};

    for (const node of nodes) {
      const d = node.data as unknown as RoleNodeData;
      roleAssembly[node.id] = {
        role: d.role,
        count: d.count,
        config: d.config,
      };
      positions[node.id] = { x: node.position.x, y: node.position.y };
    }

    const edgeData = edges.map((e) => ({
      source: e.source,
      target: e.target,
    }));

    onSave(roleAssembly, edgeData, positions);
    setSaveFlash(true);
    setTimeout(() => setSaveFlash(false), 2000);
  }, [nodes, edges, onSave]);

  const handleFitView = useCallback(() => {
    fitView({ padding: 0.3, duration: 300 });
  }, [fitView]);

  const hasNodes = nodes.length > 0;
  const totalWorkers = nodes.reduce(
    (sum, n) => sum + ((n.data as unknown as RoleNodeData).count ?? 0),
    0,
  );

  return (
    <div className="flex h-full">
      {/* Left: Role palette */}
      <div className="w-44 shrink-0 border-r border-border/40 flex flex-col">
        <div className="shrink-0 px-3 h-9 flex items-center border-b border-border/40">
          <h4 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
            {t('teams.dag.roleList')}
          </h4>
        </div>
        <div className="flex-1 overflow-y-auto p-1.5 space-y-px">
          {availableRoles.length === 0 ? (
            <p className="text-[11px] text-muted-foreground/60 px-2 py-4 text-center">
              {t('teams.dag.noAvailableRoles')}
            </p>
          ) : (
            availableRoles.map((role) => {
              const usageCount = roleUsageCounts[role.name] ?? 0;
              return (
                <button
                  key={role.role_id}
                  type="button"
                  onClick={() => handleAddRole(role.name)}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-primary/5 group"
                >
                  <Plus className="size-3.5 text-muted-foreground/30 group-hover:text-primary transition-colors shrink-0" />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground/70 group-hover:text-foreground">
                    {role.name}
                  </span>
                  {usageCount > 0 && (
                    <Badge variant="secondary" className="shrink-0 text-[9px] px-1 py-0 h-4">
                      {usageCount}
                    </Badge>
                  )}
                </button>
              );
            })
          )}
        </div>
        {/* Stats footer */}
        {hasNodes && (
          <div className="shrink-0 border-t border-border/40 px-3 py-2.5 min-h-[52px] text-[10px] text-muted-foreground flex items-center">
            <div className="flex-1 space-y-0.5">
              <div className="flex justify-between">
                <span>{t('teams.roles')}</span>
                <span className="font-medium text-foreground">{nodes.length}</span>
              </div>
              <div className="flex justify-between">
                <span>Workers</span>
                <span className="font-medium text-foreground">{totalWorkers}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right: Canvas + toolbar */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="flex items-center h-9 px-3 border-b border-border/40 shrink-0 gap-2">
          <span className="text-[11px] text-muted-foreground/60 mr-auto">
            {t('teams.dag.deleteHint')}
          </span>
          {hasNodes && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-1.5 text-muted-foreground"
              onClick={handleFitView}
            >
              <Maximize2 className="size-3" />
            </Button>
          )}
          <Button
            size="sm"
            className={cn(
              'h-6 gap-1 px-2.5 text-[11px] transition-all duration-300',
              saveFlash && 'bg-emerald-500 hover:bg-emerald-500 text-white',
            )}
            onClick={handleSave}
            disabled={isSaving}
          >
            {isSaving ? (
              <>
                <Save className="size-3" />
                {t('teams.dag.saving')}
              </>
            ) : saveFlash ? (
              <>
                <Check className="size-3" />
                {t('teams.dag.saved')}
              </>
            ) : (
              <>
                <Save className="size-3" />
                {t('teams.dag.save')}
              </>
            )}
          </Button>
        </div>

        {/* Flow canvas */}
        <div className="flex-1 relative">
          {hasNodes ? (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={onNodeClick}
              onNodesDelete={onNodesDelete}
              onPaneClick={onPaneClick}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.3 }}
              deleteKeyCode={['Backspace', 'Delete']}
              className="bg-background"
            >
              <Background gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                nodeColor="var(--color-primary)"
                maskColor="rgba(0,0,0,0.08)"
                className="!bg-card !border-border !rounded-lg"
                style={{ width: 120, height: 80 }}
              />
            </ReactFlow>
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <p className="text-sm text-muted-foreground">
                {t('teams.dag.noRoles')}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Exported wrapper with ReactFlowProvider
// ---------------------------------------------------------------------------

interface DagEditorProps {
  readonly team: TeamRecord;
  readonly onSave: (
    roleAssembly: Record<string, RoleSlotSpec>,
    edges: Array<{ source: string; target: string }>,
    positions: Record<string, { x: number; y: number }>,
  ) => void;
  readonly isSaving?: boolean;
  readonly selectedNodeId: string | null;
  readonly onNodeSelect: (nodeId: string | null, data?: RoleNodeData) => void;
  readonly onNodeDataChange: (nodeId: string, data: Partial<RoleNodeData>) => void;
}

export function DagEditor(props: DagEditorProps) {
  return (
    <ReactFlowProvider>
      <DagEditorInner {...props} />
    </ReactFlowProvider>
  );
}

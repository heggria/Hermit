// Custom ReactFlow node for rendering a role slot in the DAG editor.

import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

export interface RoleNodeData {
  role: string;
  count: number;
  description: string;
  config: Record<string, unknown>;
  [key: string]: unknown;
}

function RoleNodeComponent({ data, selected }: NodeProps) {
  const nodeData = data as unknown as RoleNodeData;

  return (
    <div
      className={cn(
        'rounded-xl border bg-card px-4 py-3 shadow-sm transition-all min-w-[160px] max-w-[220px]',
        selected
          ? 'border-primary ring-2 ring-primary/30'
          : 'border-border hover:border-primary/50',
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!size-2.5 !rounded-full !border-2 !border-primary !bg-background"
      />

      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-foreground leading-snug truncate">
          {nodeData.role}
        </p>
        <Badge variant="secondary" className="shrink-0 text-[10px] px-1.5">
          &times;{nodeData.count}
        </Badge>
      </div>

      {nodeData.description && (
        <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
          {nodeData.description}
        </p>
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-2.5 !rounded-full !border-2 !border-primary !bg-background"
      />
    </div>
  );
}

export const RoleNode = memo(RoleNodeComponent);

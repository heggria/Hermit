// Node detail/config panel shown when a DAG node is selected.

import { useState, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Settings2, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NumberStepper } from '@/components/ui/number-stepper';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { RoleNodeData } from './RoleNode';

interface NodeDetailPanelProps {
  readonly nodeId: string;
  readonly data: RoleNodeData;
  readonly onUpdate: (nodeId: string, patch: Partial<RoleNodeData>) => void;
  readonly onDelete: (nodeId: string) => void;
  readonly onClose: () => void;
}

export function NodeDetailPanel({
  nodeId,
  data,
  onUpdate,
  onDelete,
  onClose,
}: NodeDetailPanelProps) {
  const { t } = useTranslation();
  const [count, setCount] = useState(data.count);

  // Sync when a different node is selected
  useEffect(() => {
    setCount(data.count);
  }, [data.count, nodeId]);

  const handleCountChange = useCallback(
    (newCount: number) => {
      setCount(newCount);
      onUpdate(nodeId, { count: newCount });
    },
    [nodeId, onUpdate],
  );

  const configEntries = Object.entries(data.config ?? {});

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-9 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <Settings2 className="size-4 text-primary shrink-0" />
          <h3 className="text-sm font-semibold text-foreground truncate">
            {t('teams.nodeDetail.title')}
          </h3>
        </div>
        <Button variant="ghost" size="icon-xs" onClick={onClose}>
          <X className="size-3.5" />
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {/* Role name */}
        <div>
          <label className="text-xs font-medium text-muted-foreground">
            {t('teams.nodeDetail.roleName')}
          </label>
          <p className="mt-1 text-sm font-semibold text-foreground">{data.role}</p>
        </div>

        {/* Node ID */}
        <div>
          <label className="text-xs font-medium text-muted-foreground">
            {t('teams.nodeDetail.nodeId')}
          </label>
          <p className="mt-1 text-xs text-muted-foreground font-mono">{nodeId}</p>
        </div>

        {/* Description */}
        {data.description && (
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              {t('teams.nodeDetail.description')}
            </label>
            <p className="mt-1 text-xs text-muted-foreground">{data.description}</p>
          </div>
        )}

        {/* Worker count */}
        <div>
          <label className="text-xs font-medium text-muted-foreground">
            {t('teams.nodeDetail.workerCount')}
          </label>
          <div className="mt-2">
            <NumberStepper value={count} onChange={handleCountChange} min={1} max={99} />
          </div>
          <p className="mt-1.5 text-[10px] text-muted-foreground">
            {t('teams.nodeDetail.workerCountHint')}
          </p>
        </div>

        {/* Config */}
        {configEntries.length > 0 && (
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              {t('teams.nodeDetail.config')}
            </label>
            <div className="mt-2 space-y-1.5">
              {configEntries.map(([key, value]) => (
                <div key={key} className="flex items-center gap-2 text-xs">
                  <Badge variant="outline" className="text-[10px] font-mono shrink-0">
                    {key}
                  </Badge>
                  <span className="text-muted-foreground truncate">
                    {typeof value === 'string' ? value : JSON.stringify(value)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Footer: delete node */}
      <div className="px-4 py-3 border-t border-border">
        <Button
          variant="outline"
          size="sm"
          className={cn('w-full text-destructive hover:text-destructive')}
          onClick={() => onDelete(nodeId)}
        >
          <Trash2 className="size-3.5" />
          {t('teams.nodeDetail.deleteNode')}
        </Button>
      </div>
    </div>
  );
}

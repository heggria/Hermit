// SSE hook for live task updates — invalidates task queries on kernel events.

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { createEventSource } from '@/lib/sse';

export function useTaskStream(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const cleanup = createEventSource('/api/events', {
      task_update: () => {
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        queryClient.invalidateQueries({ queryKey: ['metrics', 'summary'] });
      },
      approval_update: () => {
        queryClient.invalidateQueries({ queryKey: ['approvals'] });
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
      },
      metrics_update: () => {
        queryClient.invalidateQueries({ queryKey: ['metrics'] });
      },
    });

    return cleanup;
  }, [queryClient]);
}

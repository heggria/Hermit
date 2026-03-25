// SSE hook for live task updates — invalidates task queries on kernel events.

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { createEventSource } from '@/lib/sse';

interface ToolActiveEvent {
  task_id: string;
  tool_name: string;
  input_summary: string;
  started_at: number;
}

export function useTaskStream(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const cleanup = createEventSource('/api/stream/events', {
      // Event names must match backend SSE event field exactly
      'task.update': () => {
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        queryClient.invalidateQueries({ queryKey: ['metrics', 'summary'] });
      },
      'approvals.pending': () => {
        queryClient.invalidateQueries({ queryKey: ['approvals'] });
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
      },
      'tool.active': (data: unknown) => {
        const event = data as ToolActiveEvent;
        if (event?.task_id) {
          queryClient.setQueryData(
            ['tasks', event.task_id, 'active-tool'],
            event,
          );
        }
      },
    });

    return cleanup;
  }, [queryClient]);
}

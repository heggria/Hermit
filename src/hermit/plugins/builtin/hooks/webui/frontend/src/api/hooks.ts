// TanStack Query hooks for all Hermit WebUI API endpoints.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  TaskRecord,
  StepRecord,
  ApprovalRecord,
  ReceiptRecord,
  MemoryRecord,
  EvidenceSignal,
  GovernanceMetrics,
  MetricsSummary,
  PluginInfo,
  ConfigStatus,
  TaskSubmitResponse,
  ApprovalActionResponse,
} from '@/types';

// ---------------------------------------------------------------------------
// Query key factories
// ---------------------------------------------------------------------------

const keys = {
  tasks: {
    all: ['tasks'] as const,
    list: (status?: string, limit?: number) => ['tasks', { status, limit }] as const,
    detail: (taskId: string) => ['tasks', taskId] as const,
    steps: (taskId: string) => ['tasks', taskId, 'steps'] as const,
    events: (taskId: string) => ['tasks', taskId, 'events'] as const,
    receipts: (taskId: string) => ['tasks', taskId, 'receipts'] as const,
    proof: (taskId: string) => ['tasks', taskId, 'proof'] as const,
  },
  approvals: {
    all: ['approvals'] as const,
    list: (status?: string, limit?: number) => ['approvals', { status, limit }] as const,
  },
  metrics: {
    governance: (hours?: number) => ['metrics', 'governance', { hours }] as const,
    summary: ['metrics', 'summary'] as const,
  },
  memories: {
    all: ['memories'] as const,
    list: (limit?: number) => ['memories', { limit }] as const,
    detail: (memoryId: string) => ['memories', memoryId] as const,
  },
  signals: {
    all: ['signals'] as const,
    list: (limit?: number) => ['signals', { limit }] as const,
  },
  config: {
    status: ['config', 'status'] as const,
    plugins: ['config', 'plugins'] as const,
  },
  policy: {
    profiles: ['policy', 'profiles'] as const,
    guards: ['policy', 'guards'] as const,
    actionClasses: ['policy', 'action-classes'] as const,
  },
};

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

export function useTaskList(status?: string, limit = 20) {
  return useQuery({
    queryKey: keys.tasks.list(status, limit),
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      params.set('limit', String(limit));
      return api<{ tasks: TaskRecord[]; count: number }>(
        `/api/tasks?${params.toString()}`,
      );
    },
    refetchInterval: 5_000,
  });
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: keys.tasks.detail(taskId),
    queryFn: () =>
      api<{ task: TaskRecord; steps: StepRecord[]; approvals: ApprovalRecord[] }>(
        `/api/tasks/${taskId}`,
      ),
    enabled: !!taskId,
    refetchInterval: 5_000,
  });
}

export function useTaskSteps(taskId: string) {
  return useQuery({
    queryKey: keys.tasks.steps(taskId),
    queryFn: () =>
      api<{ task_id: string; steps: StepRecord[]; attempts: Record<string, unknown>[] }>(
        `/api/tasks/${taskId}/steps`,
      ),
    enabled: !!taskId,
    refetchInterval: 5_000,
  });
}

export function useTaskEvents(taskId: string) {
  return useQuery({
    queryKey: keys.tasks.events(taskId),
    queryFn: () =>
      api<{ task_id: string; events: Record<string, unknown>[] }>(
        `/api/tasks/${taskId}/events`,
      ),
    enabled: !!taskId,
    refetchInterval: 10_000,
  });
}

export function useTaskReceipts(taskId: string) {
  return useQuery({
    queryKey: keys.tasks.receipts(taskId),
    queryFn: () =>
      api<{ task_id: string; receipts: ReceiptRecord[] }>(
        `/api/tasks/${taskId}/receipts`,
      ),
    enabled: !!taskId,
    refetchInterval: 10_000,
  });
}

export function useTaskProof(taskId: string) {
  return useQuery({
    queryKey: keys.tasks.proof(taskId),
    queryFn: () =>
      api<Record<string, unknown>>(`/api/tasks/${taskId}/proof`),
    enabled: !!taskId,
  });
}

export function useSubmitTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { description: string; policy_profile?: string }) =>
      api<TaskSubmitResponse>('/api/tasks', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

// Task output
export function useTaskOutput(taskId: string) {
  return useQuery({
    queryKey: ['tasks', taskId, 'output'] as const,
    queryFn: () =>
      api<{
        task_id: string;
        status: string;
        title: string;
        goal: string;
        response_text: string;
        receipts: Array<{
          action_type: string | null;
          result_code: string | null;
          result_summary: string | null;
          observed_effect_summary: string | null;
          rollback_supported: boolean;
          receipt_id: string | null;
        }>;
        total_actions: number;
      }>(`/api/tasks/${taskId}/output`),
    enabled: !!taskId,
    refetchInterval: 5_000,
  });
}

// Cancel task mutation
export function useCancelTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { taskId: string; reason?: string }) =>
      api(`/api/tasks/${params.taskId}/cancel`, {
        method: 'POST',
        body: JSON.stringify({ reason: params.reason ?? '' }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

// Rollback task mutation
export function useRollbackTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      api(`/api/tasks/${taskId}/rollback`, { method: 'POST' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

// Steer task mutation
export function useSteerTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { taskId: string; message: string }) =>
      api<{ task_id: string; directive_id: string; status: string }>(
        `/api/tasks/${params.taskId}/steer`,
        { method: 'POST', body: JSON.stringify({ message: params.message }) },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export function useApprovals(status?: string, limit = 50) {
  return useQuery({
    queryKey: keys.approvals.list(status, limit),
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      params.set('limit', String(limit));
      return api<{ approvals: ApprovalRecord[] }>(
        `/api/approvals?${params.toString()}`,
      );
    },
    refetchInterval: 5_000,
  });
}

export function useApproveMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (approvalId: string) =>
      api<ApprovalActionResponse>(`/api/approvals/${approvalId}/approve`, {
        method: 'POST',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.approvals.all });
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

export function useDenyMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { approvalId: string; reason?: string }) =>
      api<ApprovalActionResponse>(`/api/approvals/${params.approvalId}/deny`, {
        method: 'POST',
        body: JSON.stringify({ reason: params.reason ?? '' }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.approvals.all });
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Governance Metrics
// ---------------------------------------------------------------------------

export function useGovernanceMetrics(hours = 24) {
  return useQuery({
    queryKey: keys.metrics.governance(hours),
    queryFn: () =>
      api<GovernanceMetrics>(`/api/metrics/governance?hours=${hours}`),
    refetchInterval: 30_000,
  });
}

export function useMetricsSummary() {
  return useQuery({
    queryKey: keys.metrics.summary,
    queryFn: () => api<MetricsSummary>('/api/metrics/summary'),
    refetchInterval: 10_000,
  });
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export function useMemories(limit = 50) {
  return useQuery({
    queryKey: keys.memories.list(limit),
    queryFn: async () => {
      const memories = await api<MemoryRecord[]>(`/api/memories?limit=${limit}`);
      return { memories };
    },
    refetchInterval: 30_000,
  });
}

export function useMemory(memoryId: string) {
  return useQuery({
    queryKey: keys.memories.detail(memoryId),
    queryFn: () => api<MemoryRecord>(`/api/memories/${memoryId}`),
    enabled: !!memoryId,
  });
}

// ---------------------------------------------------------------------------
// Evidence Signals
// ---------------------------------------------------------------------------

export function useSignals(limit = 50) {
  return useQuery({
    queryKey: keys.signals.list(limit),
    queryFn: async () => {
      const signals = await api<EvidenceSignal[]>(`/api/signals?limit=${limit}`);
      return { signals };
    },
    refetchInterval: 10_000,
  });
}

export function useSignalAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { signalId: string; action: 'act' | 'suppress' }) =>
      api<{ status: string; signal_id: string }>(
        `/api/signals/${params.signalId}/${params.action}`,
        { method: 'POST' },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.signals.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export function useConfigStatus() {
  return useQuery({
    queryKey: keys.config.status,
    queryFn: () => api<ConfigStatus>('/api/config/status'),
    refetchInterval: 30_000,
  });
}

export function useConfigPlugins() {
  return useQuery({
    queryKey: keys.config.plugins,
    queryFn: () => api<PluginInfo[]>('/api/config/plugins'),
    refetchInterval: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Policy
// ---------------------------------------------------------------------------

export interface PolicyProfile {
  name: string;
  description: string;
  policy_mode: string;
}

export interface PolicyGuard {
  name: string;
  description: string;
  order: number;
}

export interface PolicyActionClass {
  name: string;
  risk: string;
  default_verdict: string;
}

export function usePolicyProfiles() {
  return useQuery({
    queryKey: keys.policy.profiles,
    queryFn: () => api<{ profiles: PolicyProfile[] }>('/api/policy/profiles'),
    staleTime: 30_000,
  });
}

export function usePolicyGuards() {
  return useQuery({
    queryKey: keys.policy.guards,
    queryFn: () => api<{ guards: PolicyGuard[] }>('/api/policy/guards'),
    staleTime: 60_000,
  });
}

export function usePolicyActionClasses() {
  return useQuery({
    queryKey: keys.policy.actionClasses,
    queryFn: () =>
      api<{ action_classes: PolicyActionClass[] }>('/api/policy/action-classes'),
    staleTime: 60_000,
  });
}

export function useApproveAllMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ approved: number; total: number; results: Record<string, unknown>[] }>(
        '/api/policy/approve-all',
        { method: 'POST' },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.approvals.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Receipt-level rollback
// ---------------------------------------------------------------------------

export function useRollbackReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (receiptId: string) =>
      api<Record<string, unknown>>(`/api/receipts/${receiptId}/rollback`, {
        method: 'POST',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

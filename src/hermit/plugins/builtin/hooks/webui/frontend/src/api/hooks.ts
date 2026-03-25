// TanStack Query hooks for all Hermit WebUI API endpoints.

import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  TaskRecord,
  StepRecord,
  ApprovalRecord,
  ReceiptRecord,
  ToolCallRecord,
  MemoryRecord,
  EvidenceSignal,
  GovernanceMetrics,
  MetricsSummary,
  PluginInfo,
  ConfigStatus,
  TaskSubmitResponse,
  ApprovalActionResponse,
  ProgramRecord,
  TeamRecord,
  MilestoneRecord,
  RoleDefinition,
  McpServerInfo,
  SkillInfo,
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
    toolCalls: (taskId: string) => ['tasks', taskId, 'tool-calls'] as const,
    proof: (taskId: string) => ['tasks', taskId, 'proof'] as const,
  },
  approvals: {
    all: ['approvals'] as const,
    list: (status?: string, limit?: number) => ['approvals', { status, limit }] as const,
    stats: ['approvals', 'stats'] as const,
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
    mcpServers: ['config', 'mcp-servers'] as const,
    skills: ['config', 'skills'] as const,
  },
  policy: {
    profiles: ['policy', 'profiles'] as const,
    guards: ['policy', 'guards'] as const,
    actionClasses: ['policy', 'action-classes'] as const,
  },
  programs: {
    all: ['programs'] as const,
    list: (status?: string, limit?: number) => ['programs', { status, limit }] as const,
    detail: (programId: string) => ['programs', programId] as const,
    tasks: (programId: string, status?: string, limit?: number) =>
      ['programs', programId, 'tasks', { status, limit }] as const,
    memory: (programId: string, limit?: number) =>
      ['programs', programId, 'memory', { limit }] as const,
    signals: (programId: string, limit?: number) =>
      ['programs', programId, 'signals', { limit }] as const,
    approvals: (programId: string, status?: string) =>
      ['programs', programId, 'approvals', { status }] as const,
  },
  teams: {
    all: ['teams'] as const,
    list: (programId?: string, limit?: number) => ['teams', { programId, limit }] as const,
    detail: (teamId: string) => ['teams', teamId] as const,
  },
  roles: {
    all: ['roles'] as const,
    list: (includeBuiltin?: boolean, limit?: number) =>
      ['roles', { includeBuiltin, limit }] as const,
    detail: (roleId: string) => ['roles', roleId] as const,
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
    refetchInterval: 10_000,
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
    refetchInterval: 10_000,
  });
}

export function useTaskSteps(taskId: string, poll = true) {
  return useQuery({
    queryKey: keys.tasks.steps(taskId),
    queryFn: () =>
      api<{ task_id: string; steps: StepRecord[]; attempts: Record<string, unknown>[] }>(
        `/api/tasks/${taskId}/steps`,
      ),
    enabled: !!taskId,
    refetchInterval: poll ? 5_000 : false,
    staleTime: poll ? undefined : 30_000,
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
    refetchInterval: 15_000,
  });
}

export function useTaskReceipts(taskId: string, poll = true) {
  return useQuery({
    queryKey: keys.tasks.receipts(taskId),
    queryFn: () =>
      api<{ task_id: string; receipts: ReceiptRecord[] }>(
        `/api/tasks/${taskId}/receipts`,
      ),
    enabled: !!taskId,
    refetchInterval: poll ? 10_000 : false,
    staleTime: poll ? undefined : 30_000,
  });
}

export function useToolCalls(taskId: string, poll = true) {
  return useQuery({
    queryKey: keys.tasks.toolCalls(taskId),
    queryFn: () =>
      api<{ task_id: string; tool_calls: ToolCallRecord[]; total: number }>(
        `/api/tasks/${taskId}/tool-calls`,
      ),
    enabled: !!taskId,
    refetchInterval: poll ? 5_000 : false,
    staleTime: poll ? undefined : 30_000,
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
    mutationFn: (body: { description: string; policy_profile?: string; attachments?: string[] }) =>
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
export function useTaskOutput(taskId: string, poll = true) {
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
          action_label?: string;
          result_code: string | null;
          result_summary: string | null;
          observed_effect_summary: string | null;
          rollback_supported: boolean;
          receipt_id: string | null;
        }>;
        total_actions: number;
      }>(`/api/tasks/${taskId}/output`),
    enabled: !!taskId,
    refetchInterval: poll ? 10_000 : false,
    staleTime: poll ? undefined : 30_000,
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
    refetchInterval: 10_000,
  });
}

export interface ApprovalStats {
  total: number;
  pending: number;
  approved: number;
  denied: number;
  recent_24h: number;
}

export function useApprovalStats() {
  return useQuery({
    queryKey: keys.approvals.stats,
    queryFn: () => api<ApprovalStats>('/api/approvals/stats'),
    refetchInterval: 10_000,
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

export interface MemoryStats {
  total: number;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  avg_confidence: number;
  high_confidence_count: number;
  low_confidence_count: number;
  evidence_backed_count: number;
  recent_promotions: number;
}

export function useMemoryStats() {
  return useQuery({
    queryKey: ['memory', 'stats'] as const,
    queryFn: () => api<MemoryStats>('/api/memory/stats'),
    refetchInterval: 30_000,
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
      queryClient.invalidateQueries({ queryKey: ['signals', 'stats'] });
    },
  });
}

export interface SignalStats {
  total: number;
  pending_count: number;
  high_risk_count: number;
  avg_confidence: number;
  recent_count: number;
  by_disposition: Record<string, number>;
  by_risk: Record<string, number>;
  by_source: Record<string, number>;
}

export function useSignalStats() {
  return useQuery({
    queryKey: ['signals', 'stats'] as const,
    queryFn: () => api<SignalStats>('/api/signals/stats'),
    refetchInterval: 15_000,
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

// ---------------------------------------------------------------------------
// Programs
// ---------------------------------------------------------------------------

export function useProgramList(status?: string, limit = 50) {
  return useQuery({
    queryKey: keys.programs.list(status, limit),
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      params.set('limit', String(limit));
      return api<{ programs: ProgramRecord[]; count: number }>(
        `/api/programs?${params.toString()}`,
      );
    },
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });
}

export function useProgram(programId: string) {
  return useQuery({
    queryKey: keys.programs.detail(programId),
    queryFn: () => api<{ program: ProgramRecord }>(`/api/programs/${programId}`),
    enabled: !!programId,
  });
}

export function useCreateProgram() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { title: string; goal: string; description?: string; priority?: string }) =>
      api<{ program_id: string }>('/api/programs', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.programs.all });
    },
  });
}

export function useUpdateProgramStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { programId: string; status: string }) =>
      api<{ program_id: string; status: string }>(
        `/api/programs/${params.programId}/status`,
        { method: 'POST', body: JSON.stringify({ status: params.status }) },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.programs.all });
    },
  });
}

export function useProgramTasks(programId: string, status?: string, limit = 50) {
  return useQuery({
    queryKey: keys.programs.tasks(programId, status, limit),
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      params.set('limit', String(limit));
      return api<{ tasks: TaskRecord[]; count: number }>(
        `/api/programs/${programId}/tasks?${params.toString()}`,
      );
    },
    enabled: !!programId,
    refetchInterval: 10_000,
  });
}

export function useProgramMemory(programId: string, limit = 50) {
  return useQuery({
    queryKey: keys.programs.memory(programId, limit),
    queryFn: () =>
      api<{ memories: MemoryRecord[] }>(
        `/api/programs/${programId}/memory?limit=${limit}`,
      ),
    enabled: !!programId,
    refetchInterval: 30_000,
  });
}

export function useProgramSignals(programId: string, limit = 50) {
  return useQuery({
    queryKey: keys.programs.signals(programId, limit),
    queryFn: () =>
      api<{ signals: EvidenceSignal[] }>(
        `/api/programs/${programId}/signals?limit=${limit}`,
      ),
    enabled: !!programId,
    refetchInterval: 10_000,
  });
}

export function useProgramApprovals(programId: string, status?: string) {
  return useQuery({
    queryKey: keys.programs.approvals(programId, status),
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      return api<{ approvals: ApprovalRecord[] }>(
        `/api/programs/${programId}/approvals?${params.toString()}`,
      );
    },
    enabled: !!programId,
    refetchInterval: 10_000,
  });
}

export function useSubmitProgramTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      programId: string;
      description: string;
      policy_profile?: string;
      team_id?: string;
    }) =>
      api<TaskSubmitResponse>(`/api/programs/${params.programId}/tasks`, {
        method: 'POST',
        body: JSON.stringify({
          description: params.description,
          policy_profile: params.policy_profile,
          team_id: params.team_id,
        }),
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: keys.programs.tasks(variables.programId),
      });
      queryClient.invalidateQueries({ queryKey: keys.tasks.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

export function useTeamList(programId?: string, limit = 50) {
  return useQuery({
    queryKey: keys.teams.list(programId, limit),
    queryFn: () => {
      const params = new URLSearchParams();
      if (programId) params.set('program_id', programId);
      params.set('limit', String(limit));
      return api<{ teams: TeamRecord[]; count: number }>(
        `/api/teams?${params.toString()}`,
      );
    },
    refetchInterval: 10_000,
  });
}

export function useTeam(teamId: string) {
  return useQuery({
    queryKey: keys.teams.detail(teamId),
    queryFn: () =>
      api<{ team: TeamRecord; milestones: MilestoneRecord[] }>(
        `/api/teams/${teamId}`,
      ),
    enabled: !!teamId,
  });
}

export function useCreateTeam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      program_id?: string;
      title: string;
      role_assembly?: Record<string, unknown>;
    }) =>
      api<{ team_id: string }>('/api/teams', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.teams.all });
    },
  });
}

export function useUpdateTeam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      teamId: string;
      title?: string;
      role_assembly?: Record<string, unknown>;
      metadata?: Record<string, unknown>;
      status?: string;
    }) => {
      const { teamId, ...body } = params;
      return api<{ team_id: string }>(`/api/teams/${teamId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.teams.all });
    },
  });
}

export function useDeleteTeam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (teamId: string) =>
      api<{ status: string }>(`/api/teams/${teamId}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.teams.all });
    },
  });
}

export function useCreateMilestone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      team_id: string;
      title: string;
      description?: string;
      dependency_ids?: string[];
      acceptance_criteria?: string[];
    }) =>
      api<{ milestone_id: string }>(`/api/teams/${body.team_id}/milestones`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: keys.teams.detail(variables.team_id),
      });
    },
  });
}

export function useUpdateMilestone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      teamId: string;
      milestoneId: string;
      title?: string;
      description?: string;
      status?: string;
    }) => {
      const { teamId, milestoneId, ...body } = params;
      return api<{ milestone_id: string }>(
        `/api/teams/${teamId}/milestones/${milestoneId}`,
        { method: 'PATCH', body: JSON.stringify(body) },
      );
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: keys.teams.detail(variables.teamId),
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Roles
// ---------------------------------------------------------------------------

export function useRoleList(includeBuiltin = true, limit = 100) {
  return useQuery({
    queryKey: keys.roles.list(includeBuiltin, limit),
    queryFn: () => {
      const params = new URLSearchParams();
      params.set('include_builtin', String(includeBuiltin));
      params.set('limit', String(limit));
      return api<{ roles: RoleDefinition[]; count: number }>(
        `/api/roles?${params.toString()}`,
      );
    },
    refetchInterval: 30_000,
  });
}

export function useRole(roleId: string) {
  return useQuery({
    queryKey: keys.roles.detail(roleId),
    queryFn: () => api<{ role: RoleDefinition }>(`/api/roles/${roleId}`),
    enabled: !!roleId,
  });
}

export function useCreateRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      description?: string;
      mcp_servers?: string[];
      skills?: string[];
      config?: Record<string, unknown>;
    }) =>
      api<{ role_id: string }>('/api/roles', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.roles.all });
    },
  });
}

export function useUpdateRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      roleId: string;
      name?: string;
      description?: string;
      mcp_servers?: string[];
      skills?: string[];
      config?: Record<string, unknown>;
    }) => {
      const { roleId, ...body } = params;
      return api<{ role_id: string }>(`/api/roles/${roleId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.roles.all });
    },
  });
}

export function useDeleteRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (roleId: string) =>
      api<{ status: string }>(`/api/roles/${roleId}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.roles.all });
    },
  });
}

// ---------------------------------------------------------------------------
// MCP Servers
// ---------------------------------------------------------------------------

export function useMcpServers() {
  return useQuery({
    queryKey: keys.config.mcpServers,
    queryFn: () => api<McpServerInfo[]>('/api/config/mcp-servers'),
    staleTime: 30_000,
  });
}

export function useCreateMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      transport: string;
      description?: string;
      command?: string;
      args?: string[];
      env?: Record<string, string>;
      url?: string;
      headers?: Record<string, string>;
      allowed_tools?: string[];
      auth?: { type: string; env_key?: string; token_url?: string };
    }) =>
      api<{ name: string; needs_reload: boolean }>('/api/mcp-servers', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: async (data) => {
      if (data.needs_reload) {
        await api('/api/mcp-servers/reload', { method: 'POST' }).catch(() => {});
      }
      queryClient.invalidateQueries({ queryKey: keys.config.mcpServers });
    },
  });
}

export function useUpdateMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      name: string;
      description?: string;
      command?: string;
      args?: string[];
      env?: Record<string, string>;
      url?: string;
      headers?: Record<string, string>;
      allowed_tools?: string[];
    }) => {
      const { name, ...body } = params;
      return api<{ name: string; needs_reload: boolean }>(
        `/api/mcp-servers/${encodeURIComponent(name)}`,
        { method: 'PATCH', body: JSON.stringify(body) },
      );
    },
    onSuccess: async (data) => {
      if (data.needs_reload) {
        await api('/api/mcp-servers/reload', { method: 'POST' }).catch(() => {});
      }
      queryClient.invalidateQueries({ queryKey: keys.config.mcpServers });
    },
  });
}

export function useReloadMcpServers() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ status: string }>('/api/mcp-servers/reload', { method: 'POST' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.config.mcpServers });
    },
  });
}

export function useDeleteMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<{ status: string; needs_reload?: boolean }>(
        `/api/mcp-servers/${encodeURIComponent(name)}`,
        { method: 'DELETE' },
      ),
    onSuccess: async (data) => {
      if (data.needs_reload) {
        await api('/api/mcp-servers/reload', { method: 'POST' }).catch(() => {});
      }
      queryClient.invalidateQueries({ queryKey: keys.config.mcpServers });
    },
  });
}

export function useUpdateMcpServerEnv() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { name: string; key: string; value: string }) => {
      const { name, ...body } = params;
      return api<{ name: string; needs_reload: boolean }>(
        `/api/mcp-servers/${encodeURIComponent(name)}/env`,
        { method: 'PATCH', body: JSON.stringify(body) },
      );
    },
    onSuccess: async (data) => {
      if (data.needs_reload) {
        await api('/api/mcp-servers/reload', { method: 'POST' }).catch(() => {});
      }
      queryClient.invalidateQueries({ queryKey: keys.config.mcpServers });
    },
  });
}

export function useStartMcpOAuth() {
  return useMutation({
    mutationFn: (params: { name: string; server_url?: string }) => {
      const { name, ...body } = params;
      return api<{ auth_url: string }>(
        `/api/mcp-servers/${encodeURIComponent(name)}/oauth/start`,
        { method: 'POST', body: JSON.stringify(body) },
      );
    },
  });
}

export function useClearMcpOAuth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<{ status: string; needs_reload?: boolean }>(
        `/api/mcp-servers/${encodeURIComponent(name)}/oauth`,
        { method: 'DELETE' },
      ),
    onSuccess: async (data) => {
      if (data.needs_reload) {
        await api('/api/mcp-servers/reload', { method: 'POST' }).catch(() => {});
      }
      queryClient.invalidateQueries({ queryKey: keys.config.mcpServers });
    },
  });
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export function useSkills() {
  return useQuery({
    queryKey: keys.config.skills,
    queryFn: () => api<SkillInfo[]>('/api/config/skills'),
    staleTime: 30_000,
  });
}

export function useCreateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      description?: string;
      content?: string;
      max_tokens?: number;
    }) =>
      api<{ name: string; needs_reload: boolean }>('/api/skills', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.config.skills });
    },
  });
}

export function useUpdateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      name: string;
      description?: string;
      content?: string;
      max_tokens?: number;
    }) => {
      const { name, ...body } = params;
      return api<{ name: string; needs_reload: boolean }>(
        `/api/skills/${encodeURIComponent(name)}`,
        { method: 'PATCH', body: JSON.stringify(body) },
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.config.skills });
    },
  });
}

export function useDeleteSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<{ status: string }>(
        `/api/skills/${encodeURIComponent(name)}`,
        { method: 'DELETE' },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.config.skills });
    },
  });
}

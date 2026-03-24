// TypeScript types matching Hermit kernel Python data models.

export interface TaskRecord {
  task_id: string;
  conversation_id: string;
  title: string;
  goal: string;
  status: string;
  priority: string;
  source_channel: string;
  parent_task_id: string | null;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  budget_tokens_used: number;
  budget_tokens_limit: number | null;
  policy_profile: string;
}

export interface StepRecord {
  step_id: string;
  task_id: string;
  kind: string;
  status: string;
  attempt: number;
  node_key: string | null;
  title: string | null;
  depends_on: string[];
  started_at: number | null;
  finished_at: number | null;
}

export interface ApprovalRecord {
  approval_id: string;
  task_id: string;
  step_id: string;
  status: string;
  approval_type: string;
  requested_action: Record<string, unknown>;
  requested_at: number | null;
  resolved_at: number | null;
  resolved_by_principal_id: string | null;
}

export interface ReceiptRecord {
  receipt_id: string;
  task_id: string;
  step_id: string;
  action_type: string;
  result_code: string;
  result_summary: string;
  rollback_supported: boolean;
  rollback_status: string;
  created_at: number | null;
}

export interface MemoryRecord {
  memory_id: string;
  task_id: string;
  category: string;
  claim_text: string;
  confidence: number;
  importance: number;
  status: string;
  evidence_refs: string[];
  created_at: number | null;
}

export interface EvidenceSignal {
  signal_id: string;
  source_kind: string;
  summary: string;
  confidence: number;
  risk_level: string;
  disposition: string;
  suggested_goal: string;
  created_at: number;
  expires_at: number | null;
}

export interface GovernanceMetrics {
  task_throughput: number;
  approval_rate: number;
  avg_approval_latency: number;
  rollback_rate: number;
  tool_usage_counts: Record<string, number>;
  action_class_distribution: Record<string, number>;
  risk_entries: RiskEntry[];
  window_start: number;
  window_end: number;
}

export interface RiskEntry {
  action_type: string;
  risk_level: string | null;
  result_code: string | null;
  rollback_supported: boolean;
}

export interface MetricsSummary {
  total: number;
  by_status: Record<string, number>;
}

export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  builtin: boolean;
}

export interface ConfigStatus {
  host: string;
  port: number;
  uptime: number;
  pid: number;
}

export interface TaskSubmitResponse {
  task_id: string | null;
  session_id: string;
  status: string;
  policy_profile: string;
}

export interface ApprovalActionResponse {
  status: string;
  approval_id: string;
  text: string;
}

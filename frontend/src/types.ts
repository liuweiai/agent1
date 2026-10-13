export type RunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'executing'
  | 'completed'
  | 'rejected'
  | 'failed'

export interface TimelineEvent {
  id: number
  run_id: string
  step: string
  title: string
  status: 'running' | 'completed' | 'waiting' | 'failed' | 'skipped'
  detail: string
  payload: Record<string, unknown>
  created_at: string
}

export interface Evidence {
  id: string
  source_type: 'mes' | 'alarm' | 'quality' | 'maintenance' | 'knowledge' | 'analytics' | 'graph' | 'a2a'
  title: string
  excerpt: string
  source_ref: string
  score: number
}

export interface ProductionAnalysis {
  method: string
  window_size: number
  release_threshold: number
  trend: Array<{
    batch_id: string
    started_at: string
    yield_rate: number
    defect_rate: number
    marking_speed?: number | null
    laser_power_percent?: number | null
    temperature_c?: number | null
    defect_type?: string | null
  }>
  baseline: { count: number; average_yield?: number | null; average_defect_rate?: number | null }
  anomaly: { count: number; average_yield?: number | null; average_defect_rate?: number | null }
  yield_change_pp?: number | null
  correlations: Record<string, {
    label: string
    coefficient?: number | null
    interpretation: string
  }>
  alarm_overlap: {
    window_hours: number
    overlap: boolean
    matched_alarm_count: number
    closest_delta_minutes?: number | null
  }
  evidence_quality: { sufficient: boolean; limitations: string[] }
  recovery_validation: {
    status: 'pending' | 'observing' | 'passed'
    threshold: number
    required_consecutive_batches: number
    observed_batches?: number[]
    current_consecutive_batches?: number
    release_condition_met?: boolean
  }
}

export interface Hypothesis {
  cause: string
  confidence: number
  reasoning: string
  evidence_ids: string[]
}

export interface ActionItem {
  tool: string
  title: string
  description: string
  risk: 'low' | 'medium' | 'high'
  parameters: Record<string, unknown>
}

export interface RunSummary {
  id: string
  query: string
  line_id: string
  status: RunStatus
  severity?: string
  risk_level?: string
  created_at: string
  updated_at: string
}

export interface RunDetail extends RunSummary {
  state: {
    evidence?: Evidence[]
    hypotheses?: Hypothesis[]
    action_plan?: ActionItem[]
    quality?: Array<{
      batch_id: string
      started_at: string
      yield_rate: number
      marking_speed: number
      laser_power_percent?: number
      temperature_c?: number
      defect_type: string
    }>
    production_analysis?: ProductionAnalysis
    graph_context?: Array<Record<string, unknown>>
    vendor_expert?: Record<string, unknown>
    verification?: {
      mode: string
      write_state_verified: boolean
      failure_reasons?: string[]
      manual_intervention_required?: boolean
      next_steps?: string[]
      baseline_yield: number
      verification_batches: number[]
      release_condition_met: boolean
      note: string
    }
    action_results?: Array<Record<string, unknown>>
  }
  events: TimelineEvent[]
  approval?: {
    approved: boolean
    approver: string
    comment: string
    decided_at: string
  }
  report?: {
    report_id: string
    outcome?: 'completed' | 'rejected' | 'failed'
    conclusion: string
    evidence_count: number
    data_notice: string
  }
}

export interface AuthUser {
  username: string
  display_name: string
  roles: string[]
}

export interface AuditLog {
  id: number
  run_id: string
  actor: string
  tool: string
  operation: string
  parameters: Record<string, unknown>
  outcome: string
  duration_ms: number
  created_at: string
}

export interface EvaluationProfile {
  version: string
  status: string
  sample_size: number
  complete_task_success_rate: number
  grounded_hypothesis_rate: number
  tool_selection_accuracy: number
  model_success_rate?: number | null
  p95_response_time_ms: number
  average_tokens: number
  average_cost_usd?: number | null
}

export interface EvaluationReport {
  generated_at: string
  profile: string
  dataset_size: number
  metrics: {
    intent_classification_accuracy: number
    retrieval_recall_at_4: number
    scenario_pass_rate: number
    grounded_hypothesis_rate: number
    tool_selection_accuracy: number
    complete_task_success_rate: number
    high_risk_violation_rate: number
    p95_response_time_ms: number
  }
  counts: {
    intent_cases: number
    retrieval_cases: number
    scenario_cases: number
    security_cases: number
  }
  profile_comparison: EvaluationProfile[]
  langsmith_experiment?: {
    dataset: string
    experiment: string
    experiment_url: string
    run_count: number
    ingestion_complete: boolean
  }
  failed_cases: Array<{
    id: string
    scenario: string
    expected: string
    actual: string
    passed: boolean
  }>
  limitations: string[]
}

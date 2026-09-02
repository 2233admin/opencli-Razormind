export interface WorkspaceSettingsValues {
  theme: "system" | "dark" | "light";
  motion_enabled: boolean;
  sidebar_mode: "expanded" | "icon" | "collapsed";
  timezone: string;
  landing_page: "/dashboard" | "/canvas" | "/inbox";
  default_concurrency: number;
  automatic_retries: boolean;
  retain_raw_data: boolean;
  retention_days: 7 | 30 | 90 | 365;
  inbox_alerts: boolean;
  failure_alerts: boolean;
  agent_alerts: boolean;
}

export interface WorkspaceSettingsRead {
  values: WorkspaceSettingsValues;
  sources: Record<keyof WorkspaceSettingsValues, "default" | "override">;
  apply_modes: Record<keyof WorkspaceSettingsValues, "immediate" | "next_run">;
  revision: number;
  updated_at: string | null;
}
// Self-hosted LLM-provider runtime (model-provider runtime, backend/llm/, no litellm) — mirrors
// backend.schemas.provider.ModelProviderRead.from_model exactly. The raw
// api_key is NEVER returned by the backend (from_model explicitly masks it) —
// only has_api_key / api_key_preview (e.g. "sk-...wxyz", null if unset) ever
// reach the frontend. See ModelProviderInput for the write-only request body.
export interface ModelProvider {
  id: string;
  name: string;
  provider_type: "claude" | "openai" | "local";
  base_url: string | null;
  has_api_key: boolean;
  api_key_preview: string | null;
  default_model: string | null;
  notes: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

// Write-only request body for POST /providers and PATCH /providers/{id}
// (ModelProviderCreate / ModelProviderUpdate on the backend share this same
// shape, all-optional so PATCH can omit any field). `api_key` is the only
// place a raw key is ever sent — it is never echoed back on read (see
// ModelProvider.api_key_preview for the masked view).
export interface ModelProviderInput {
  name?: string;
  provider_type?: ModelProvider["provider_type"];
  base_url?: string;
  api_key?: string;
  default_model?: string;
  notes?: string;
  enabled?: boolean;
}

export interface ProviderModelDiscoveryInput {
  provider_type: ModelProvider["provider_type"];
  base_url?: string;
  api_key?: string;
}

// One row of a provider's model catalog — mirrors backend.schemas.provider.
// ProviderModelRead. `source` distinguishes rows discovered via
// POST /providers/{id}/models/sync from ones added manually through
// POST /providers/{id}/models — sync is idempotent and never touches
// 'manual' rows.
export interface ProviderModelRead {
  id: string;
  provider_id: string;
  model_id: string;
  model_type: string;
  capabilities: Record<string, unknown> | null;
  source: "discovered" | "manual";
  enabled: boolean;
  created_at: string;
}

// POST /providers/{id}/models/sync response — mirrors backend.schemas.
// provider.SyncResult. All-int and always present (unlike
// ConnectionTestResult, which is a TypedDict with optional fields).
export interface ProviderModelSyncResult {
  added: number;
  updated: number;
  kept_manual: number;
  pruned: number;
}

// POST /providers/{id}/test response — mirrors backend.llm's
// ConnectionTestResult TypedDict (total=False), so every field beyond `ok`
// is optional: an ordinary connection failure comes back as a 200 with
// ok:false and no other fields populated, this endpoint never throws for a
// probe failure (it can still 404 if the provider id doesn't exist).
export interface ConnectionTestResult {
  ok: boolean;
  latency_ms?: number | null;
  error?: string | null;
  models_sample?: string[] | null;
}

export interface FeedProviderConfig {
  timeout_seconds: number;
  allowed_domains: string[];
  allow_private_network: boolean;
  browser_routes: boolean;
  authenticated_routes: boolean;
}

export interface FeedProvider {
  id: string;
  name: string;
  provider_type: "rsshub" | "rss_bridge";
  base_url: string;
  has_access_token: boolean;
  access_token_preview: string | null;
  config: FeedProviderConfig;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface FeedProviderInput {
  name?: string;
  provider_type?: FeedProvider["provider_type"];
  base_url?: string;
  access_token?: string;
  config?: Partial<FeedProviderConfig>;
  enabled?: boolean;
}

export interface FeedProviderConnectionTest {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  error_kind: string | null;
  capabilities: Record<string, boolean>;
}

export interface FeedProviderWorkflowNodeInput {
  route?: string;
  bridge?: string;
  parameters?: Record<string, string>;
  source_group: string;
  site: string;
  max_entries?: number;
}

export interface FeedProviderWorkflowNode {
  nodeType: "intelligence.source.rss";
  label: string;
  params: {
    feedUrl: string;
    sourceGroup: string;
    site: string;
    maxEntries: number;
    providerId: string;
    generatorType: FeedProvider["provider_type"];
    generatorSelection: Record<string, unknown>;
  };
  allowedDomains: string[];
}

export interface DeliveryConnection {
  id: string
  name: string
  provider: 'feishu_bitable'
  app_id_preview: string
  has_app_secret: boolean
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface DeliveryConnectionInput {
  name?: string
  app_id?: string
  app_secret?: string
  enabled?: boolean
}

export interface FeishuBitableProbeInput {
  app_token: string
  table_id: string
}

export interface FeishuBitableProbeResult {
  ok: boolean
  field_count: number
}

// Role a model-defaults candidate list resolves for (backend.llm role
// registry): chat = agent 坞对话模型, executor = skill_channel 执行模型
// （轻量/低成本）, enrichment = pipeline 富化兜底模型.
export type ModelRole = "chat" | "executor" | "enrichment";

export interface ModelDefaultCandidate {
  provider_id: string;
  model_id: string;
}

// GET /model-defaults / PUT /model-defaults/{role} — mirrors backend.schemas.
// provider.ModelDefaultRead. A role can be entirely absent from the GET list
// (fresh install, no row created yet) — render that as "no candidates
// configured", never crash on the missing entry.
export interface ModelDefaultRead {
  id: string;
  role: ModelRole;
  candidates: ModelDefaultCandidate[];
  created_at: string;
  updated_at: string;
}

export interface AIAgent {
  id: string;
  name: string;
  description?: string;
  processor_type: "claude" | "openai" | "local";
  model?: string;
  prompt_template: string;
  processor_config: Record<string, unknown>;
  enabled: boolean;
  provider_id?: string;
  created_at: string;
  updated_at: string;
}

export interface PaginationMeta {
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T;
  error?: string;
  meta?: PaginationMeta;
}

export interface DataSource {
  id: string
  name: string
  description?: string
  channel_type:
    | 'opencli'
    | 'web_scraper'
    | 'api'
    | 'rss'
    | 'cli'
    | 'skill'
    | 'crawl4ai'
    | 'browser_act'
    | 'doubao_research'
    | 'douyin_detail'
  channel_config: Record<string, unknown>
  ai_config?: Record<string, unknown>
  enabled: boolean
  tags: string[]
  // Issue 02: the raw stored per-source SourceObjective override, null when
  // none is set — the UNRESOLVED override dict. See SourceControlState.objective
  // for the RESOLVED shape (override merged over defaults) control-state
  // actually classifies against. Optional so this type stays valid against
  // any DataSource response predating issue 02.
  objective_override?: Record<string, unknown> | null;
  // Issue 03 (Control Cycle + Actuator): set by an executed require_review
  // action; a human clears it, the Control Cycle never does.
  review_required?: boolean;
  // Issue 03: set alongside enabled=false by an executed pause action; null
  // once resumed (manually or by the Control Cycle's TTL auto-resume).
  paused_until?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RssCatalogImportResult {
  created: DataSource[];
  skipped_existing: string[];
}

// A distilled browser skill (record→distill→execute→correct loop, ADR-0003).
// `list` only ever returns the brief projection (no skill_md/elements/evidence
// body); `detail` (GET /skills/{id}) returns every field.
export interface SkillEvidenceEntry {
  event: string;
  at?: string;
  [key: string]: unknown;
}

export interface Skill {
  id: string;
  domain: string;
  capability: string;
  name: string;
  version: number;
  status: string;
  enabled: boolean;
  evidence_count: number;
  has_open_proposal: boolean;
  scope?: string | null;
  skill_md?: string;
  elements?: Record<string, string[]>;
  source_trace?: string | null;
  distill_model?: string | null;
  evidence?: SkillEvidenceEntry[];
  last_failing_trace?: Record<string, unknown> | null;
}

/** Compact projection returned by list and correction-dismiss endpoints. */
export type SkillBrief = Pick<
  Skill,
  'id' | 'domain' | 'capability' | 'name' | 'version' | 'status' | 'enabled' | 'evidence_count' | 'has_open_proposal'
>

export interface CollectionTask {
  id: string;
  source_id: string;
  source_name?: string;
  agent_id?: string;
  trigger_type: string;
  parameters: Record<string, unknown>;
  priority: number;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  error_message?: string;
  retry_of_task_id?: string | null;
  recovery_mode?: string | null;
  recovery_reason?: string | null;
  initiating_actor?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskRun {
  id: string;
  task_id: string;
  status: string;
  worker_id?: string;
  celery_task_id?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  records_collected: number;
  error_message?: string;
  created_at: string;
}

export interface TaskRunEvent {
  id: string;
  run_id: string;
  level: "info" | "warning" | "error";
  step: string;
  message: string;
  detail?: Record<string, unknown>;
  elapsed_ms?: number;
  created_at: string;
}

export interface CollectedRecord {
  id: string;
  task_id: string;
  source_id: string;
  workflow_id?: string | null;
  workflow_run_id?: string | null;
  raw_data: Record<string, unknown>;
  normalized_data: Record<string, unknown>;
  ai_enrichment?: Record<string, unknown>;
  content_hash: string;
  status: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export interface CronSchedule {
  id: string;
  source_id: string;
  agent_id?: string;
  name: string;
  cron_expression: string;
  timezone: string;
  parameters: Record<string, unknown>;
  enabled: boolean;
  is_one_time: boolean;
  last_run_at?: string;
  next_run_at?: string;
  created_at: string;
  updated_at: string;
}

export interface NotificationRule {
  id: string;
  name: string;
  source_id?: string;
  trigger_event: string;
  notifier_type: string;
  notifier_config: Record<string, unknown>;
  filter_conditions?: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

// Write-only request body for POST /notifications/rules and PATCH
// /notifications/rules/{id} (NotificationRuleCreate / NotificationRuleUpdate
// on the backend). All fields optional here so PATCH can omit any of them —
// POST additionally requires name/trigger_event/notifier_type, enforced by
// the backend schema, not this type.
//
// trigger_event is limited to the events currently produced by the pipeline:
// new records, completed AI processing, and failed tasks.
//
// source_id is accepted by NotificationRuleCreate but is deliberately absent
// from NotificationRuleUpdate on the backend — a rule's source filter can be
// set at creation but not changed afterward via this API. Omit it from PATCH
// payloads (an included value would silently be dropped by the backend's
// exclude_unset handling anyway).
export interface NotificationRuleInput {
  name?: string
  source_id?: string | null
  trigger_event?: 'on_new_record' | 'on_ai_processed' | 'on_task_failed'
  notifier_type?: string
  notifier_config?: Record<string, unknown>
  filter_conditions?: Record<string, unknown> | null
  enabled?: boolean
}

export interface NotificationLog {
  id: string;
  rule_id: string;
  record_id?: string;
  status: string;
  response_data?: Record<string, unknown>;
  error_message?: string;
  ack_status: string;
  ack_data?: Record<string, unknown>;
  acked_at?: string;
  created_at: string;
}

export * from "./browser-pool-types";
export * from "./browser-runtime-types";

export interface WorkerNode {
  id: string;
  worker_id: string;
  hostname: string;
  status: string;
  active_tasks: number;
  last_heartbeat?: string | null;
  concurrency?: number | null;
  celery_version?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface EdgeNode {
  id: string;
  url: string;
  label: string;
  protocol: "http" | "ws";
  mode: "bridge" | "cdp";
  node_type: "docker" | "shell";
  status: "online" | "offline";
  last_seen_at?: string | null;
  ip?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EdgeNodeEvent {
  id: string;
  node_id: string;
  event: "registered" | "online" | "offline";
  ip?: string | null;
  event_meta?: Record<string, unknown> | null;
  created_at: string;
}

export interface SystemConfig {
  app_name: string;
  app_env: string;
  debug: boolean;
  collection_mode: "local" | "agent";
  collection_orchestrator: "admin" | "iii";
  task_executor: "local" | "celery";
  local_max_concurrent_pipelines: number;
  opencli_timeout: number;
  default_timezone: string;
  public_url: string;
  fleet_network_provider: "lan" | "netbird" | "wireguard" | "ssh" | "custom";
  netbird_mode: "off" | "host" | "docker";
  opencli_cdp_endpoint: string;
  agent_pool_endpoints: string[];
  llm_request_timeout_seconds: number;
  llm_max_concurrency: number;
  control_mode: "advisory" | "automatic";
  control_kill_switch: boolean;
  image_tag: string;
  database_kind: string;
  api_auth_configured: boolean;
  oidc_configured: boolean;
  smtp_configured: boolean;
  credential_encryption_configured: boolean;
}

export interface NodeStats {
  total: number;
  success: number;
  failed: number;
  success_rate: number;
  records_collected: number;
}

export interface DashboardStats {
  sources: { total: number; enabled: number; disabled: number };
  tasks: { total: number; running: number; failed: number };
  runs: {
    total: number;
    success: number;
    failed: number;
    success_rate: number;
  };
  records: { total: number; ai_processed: number };
  delivery: {
    attempts: number;
    submitted: number;
    awaiting_ack: number;
    confirmed: number;
    ack_failed: number;
    submission_failed: number;
    ack_not_required: number;
    window: string;
    since?: string | null;
    until?: string | null;
  };
  recent_runs: Array<{
    id: string;
    task_id: string;
    task_trigger_type: string;
    source_name: string;
    status: string;
    records_collected: number;
    duration_ms?: number;
    created_at: string;
  }>;
}

export interface DailyActivity {
  date: string;
  total_runs: number;
  success_runs: number;
  failed_runs: number;
  new_records: number;
}

export interface DashboardActivity {
  daily: DailyActivity[];
}

export interface OpinionMonitorRecord {
  id: string;
  source_id: string;
  source_name: string;
  title: string;
  url?: string | null;
  summary: string;
  tags: string[];
  sentiment: string;
  status: string;
  notification_status: "sent" | "failed" | "pending";
  notification_channels?: string[];
  created_at: string;
}

export interface OpinionMonitor {
  window: { range: string; since?: string | null; until?: string | null };
  summary: {
    records: number;
    ai_processed: number;
    notification_sent: number;
    notification_failed: number;
    active_sources: number;
    active_notification_rules: number;
    active_notification_channels: string[];
  };
  tags: Array<{ label: string; count: number }>;
  sentiment: Array<{ label: string; count: number }>;
  sources: Array<{
    id: string;
    name: string;
    channel_type: string;
    records: number;
    ai_processed: number;
    notification_sent: number;
    notification_failed: number;
  }>;
  recent: OpinionMonitorRecord[];
}

// ── Control-state (C0 Control Room v0 — docs/CONTROL_THEORY_ARCHITECTURE.md §0) ─
// Read-only sensor-honesty view of a source: GET /sources/{id}/control-state.
// `measurement`/`control_state`/`confidence`/`sensor_coverage` are all null when
// the source has never run. The point of this shape is that an incomplete
// sensor system can never present as a confident "healthy" — see confidence +
// missing_signals, which the UI must render prominently, not as an afterthought.
export interface SourceMeasurement {
  source_id: string;
  run_id: string;
  accepted: number;
  duplicates: number;
  rejected: number;
  fetch_latency_ms: number;
  ingest_latency_ms?: number | null;
  store_latency_ms?: number | null;
  error_rate: number;
  duplicate_rate: number;
  freshness_lag_seconds?: number | null;
  cursor_advanced: boolean;
  odp_stream_lag?: number | null;
  odp_pending?: number | null;
  dlq_count: number;
  // source | observed_fallback | missing | invalid | synthetic — mirrors
  // backend.control.measurements.SourceMeasurement.source_ts_quality. Absent
  // (not just null) on measurements built from the pre-C1 TaskRunEvent
  // fallback path, which has no freshness quality signal at all.
  source_ts_quality?: string | null;
  observed_at: string;
}

export type SourceControlStateValue =
  | "healthy"
  | "degraded"
  | "backpressured"
  | "rate_limited"
  | "auth_failed"
  | "schema_drift"
  // PR-Control-3: the source itself may be fine, but the shared ODP data plane
  // is backpressured beyond objective — bottleneck is system-wide, not this
  // source. Distinct from 'backpressured' (legacy, per-measurement signal).
  | "blocked_by_odp"
  | "paused"
  | "dead"
  | "unknown";

export type SensorConfidence = "high" | "medium" | "low";

// Which sensor signals behind control_state are real vs. still a placeholder —
// see backend.control.coverage. `run` is true whenever a measurement exists;
// the other four are only true once that specific signal is actually wired up.
export interface SensorCoverage {
  run: boolean;
  cursor: boolean;
  freshness: boolean;
  error_kinds: boolean;
  odp: boolean;
}

export interface SourceObjective {
  max_error_rate: number;
  max_duplicate_rate: number;
  max_freshness_lag_seconds?: number | null;
  max_run_latency_ms: number;
  max_pending: number;
  min_accepted_per_run?: number | null;
}

// PR-Control-3 (docs/CONTROL_THEORY_ARCHITECTURE.md §4): the advisory decision
// engine's inputs/outputs, layered on top of C0/C1/C2's sensor facts. All
// fields are optional/nullable-tolerant because the backend evaluator may land
// slightly after this UI change (pinned contract, not yet shipped when this
// was written) — an unknown/missing field must never crash the render, only
// degrade to "nothing to show" (same C0 rule: silence, not a fake positive).

// Rolling-window summary from backend.control.aggregation.build_trend.
// `provenance` (issue 06, additive): present as 'run_history' only when the
// source has zero source_measurements rows and the trend was derived from
// task-run history instead; absent means measurement-backed. Display-only —
// never treat a fallback trend as full sensor coverage.
export interface SourceControlTrend {
  window: number;
  zero_accepted_streak: number;
  avg_error_rate: number;
  rate_limited_runs: number;
  provenance?: "run_history";
}

// Shared-infrastructure (ODP) context the evaluator folds in alongside the
// source's own measurement — see SourceControlStateValue.blocked_by_odp.
// `available: false` means the ODP collector itself couldn't be read (degrade
// honestly, never fabricate `odp_backpressured`).
export interface SourceSystemContext {
  odp_backpressured: boolean;
  stream_lag: number | null;
  pending: number | null;
  available: boolean;
}

// A candidate control action the evaluator would take — ADVISORY ONLY.
// control_mode 'advisory' means nothing here is ever executed automatically;
// there is intentionally no id/status/apply-endpoint on this shape because the
// UI must never offer to execute one (see ControlBadge/atoms — display only).
export interface SuggestedControlAction {
  action_type: string;
  reason: string;
  payload: Record<string, unknown>;
}

export type ControlMode = "advisory" | "automatic";

export interface SourceControlState {
  source_id: string;
  measurement: SourceMeasurement | null;
  control_state: SourceControlStateValue | null;
  objective: SourceObjective;
  confidence: SensorConfidence | null;
  sensor_coverage: SensorCoverage | null;
  missing_signals: string[];
  // PR-Control-3 additions — optional so this type stays valid against both
  // the pre-Control-3 API response and the enriched one.
  trend?: SourceControlTrend | null;
  system_context?: SourceSystemContext | null;
  suggested_actions?: SuggestedControlAction[];
  control_mode?: ControlMode | null;
}

// ── ODP system-level state (issue 07 — GET /control/odp-state) ───────────────
// Distinct from SourceSystemContext (the evaluator's folded-in per-source
// view): this is the raw system snapshot the topology ODP node renders.
// Every section carries its own `available` flag (+ optional `error`) so a
// down Redis/odp-ingest degrades that section to unavailable, never a
// fabricated healthy zero — mirrors backend/schemas/odp_state.py exactly.
export interface OdpIngestHealth {
  available: boolean;
  healthy: boolean | null;
  error?: string | null;
}

export interface OdpStreamGroupState {
  available: boolean;
  name: string;
  group: string;
  lag: number | null;
  pending: number | null;
  oldest_pending_idle_ms: number | null;
  error?: string | null;
}

export interface OdpDlqSummary {
  available: boolean;
  total: number | null;
  last_24h: number | null;
  error?: string | null;
}

export interface OdpStoreHealth {
  available: boolean;
  healthy: boolean | null;
  heartbeat_age_seconds: number | null;
  note: string | null;
  error?: string | null;
}

export interface OdpOutboxState {
  available: boolean;
  unpublished: number | null;
  note: string | null;
  error?: string | null;
}

export interface OdpSystemState {
  ingest: OdpIngestHealth;
  stream: OdpStreamGroupState;
  dlq: OdpDlqSummary;
  store: OdpStoreHealth;
  outbox: OdpOutboxState;
  collected_at: string;
}

// ── Evidence Ledger row (issue 07 — GET /control/actions) ────────────────────
// Row-level control_actions listing — mirrors backend/schemas/control.py's
// ControlActionRecordRead. `outcome`/`evaluated_at` are null until
// backend.control.outcomes judges the row ("pending" is the absence of a
// value, not a stored verdict — see the ?outcome=pending query convention).
export type ControlActionOutcome =
  "recovered" | "persisted" | "insufficient_data";

export interface ControlActionRecord {
  id: string;
  source_id: string;
  run_id: string | null;
  measurement_id: string | null;
  mode: ControlMode;
  state: string;
  action_type: string;
  reason: string | null;
  payload: Record<string, unknown>;
  executed: boolean;
  evaluated_at: string | null;
  outcome: ControlActionOutcome | null;
  outcome_detail: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

// ── Kill switch (PR-Control issue 03 — GET/POST /control/kill-switch) ────────
// `engaged` is the effective state the Control Cycle actually checks: the
// in-memory runtime override when one has been set via POST, else
// Settings.control_kill_switch. `runtime_override` is null when no runtime
// toggle has been set this process lifetime (purely following config) —
// mirrors backend/schemas/control.py's KillSwitchRead exactly.
export interface KillSwitchState {
  engaged: boolean;
  runtime_override: boolean | null;
  config_default: boolean;
}

// ── Advisory report (PR-Control-3.5 — GET /control/advisory-report) ─────────
// The gate data for ever flipping Settings.control_mode to "automatic" per
// state class. `recovery_rate` = recovered / (recovered + persisted); null
// when no row in the set has reached a recovered/persisted verdict yet — a
// 0-of-0 rate would be a fabricated signal, not a measurement. Mirrors
// backend/schemas/control.py's AdvisoryReportTotalsRead/AdvisoryReportRead.
export interface AdvisoryReportTotals {
  total: number;
  pending: number;
  evaluated: number;
  recovered: number;
  persisted: number;
  insufficient_data: number;
  recovery_rate: number | null;
}

// One (state, action_type) bucket of the advisory evidence ledger — e.g.
// "everything we suggested pause_source for while auth_failed".
export interface AdvisoryReportBucket extends AdvisoryReportTotals {
  state: string;
  action_type: string;
}

export interface AdvisoryReport {
  buckets: AdvisoryReportBucket[];
  totals: AdvisoryReportTotals;
  mode_breakdown: Record<string, number>;
  evaluation: {
    evaluated: number;
    recovered: number;
    persisted: number;
    insufficient_data: number;
    still_pending: number;
  };
}

// ── Source measurement history (Source Control Room — GET /sources/{id}/measurements) ─
// One persisted source_measurements row — the raw per-run sensor reading, NOT
// the same shape as SourceMeasurement above (that one is the in-memory,
// decision-time contract embedded in SourceControlState; this one is the
// stored DB row, with id/created_at/updated_at and the full derivation
// inputs). Mirrors backend/schemas/control.py's SourceMeasurementRecordRead.
export interface SourceMeasurementRecord {
  id: string;
  source_id: string;
  run_id: string;
  measured_at: string;
  accepted: number;
  duplicates: number;
  rejected: number;
  error_rate: number;
  duplicate_rate: number;
  error_kinds: Record<string, number>;
  fetch_latency_ms: number | null;
  ingest_latency_ms: number | null;
  store_latency_ms: number | null;
  cursor_advanced: boolean;
  newest_source_ts: string | null;
  newest_observed_at: string | null;
  freshness_lag_seconds: number | null;
  source_ts_quality: string;
  raw: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ── Plan IR (Plan IR issues 01/02/06 — docs/plan-ir-PRD.md) ────────────────────
// Mirrors backend.schemas.plan_ir.{PlanPort,PlanNode,PlanEdge,PlanGraph} and
// backend.schemas.plan.{PlanRead,...} field-for-field. This is the wire shape;
// frontend/src/lib/planCanvasModel.ts owns the pure IR↔canvas projection.

export type PlanNodeKind = "source" | "transform" | "merge" | "sink";

export interface PlanPort {
  name: string;
  type: string;
}

export interface PlanNode {
  id: string;
  kind: PlanNodeKind;
  type: string;
  label?: string | null;
  params: Record<string, unknown>;
  required_params: string[];
  inputs: PlanPort[];
  outputs: PlanPort[];
  source_id?: string | null;
  draft: boolean;
}

export interface PlanEdge {
  id: string;
  source_node: string;
  source_port: string;
  target_node: string;
  target_port: string;
}

export interface PlanGraph {
  ir_version: string;
  name?: string | null;
  draft: boolean;
  nodes: PlanNode[];
  edges: PlanEdge[];
}

export interface PlanRead {
  id: string;
  name: string;
  graph: PlanGraph;
  version: number;
  draft: boolean;
  runnable: boolean;
  created_at: string;
  updated_at: string;
}

// One node-anchored structural-validation error (backend.plan_ir.validation.
// PlanValidationError.to_dict()) — returned as the 422 `detail` array on a
// failed plan save. node_id/edge_id are the anchors the canvas renders on.
export interface PlanValidationErrorItem {
  code: string;
  message: string;
  node_id?: string;
  edge_id?: string;
}

// ── Presets (Plan IR issue 06) ──────────────────────────────────────────────────
// Mirrors backend.plan_ir.presets.Preset. Read-only, grouped by channel_type;
// the palette (issue 07) renders these dynamically — nothing hardcoded here.
export interface Preset {
  id: string;
  channel_type: string;
  node_type: string;
  label: string;
  description: string;
  params: Record<string, unknown>;
}

export type PresetsGrouped = Record<string, Preset[]>;

// ── Plan run + health (Plan IR issue 03/04/08) ───────────────────────────────
// Mirrors backend.schemas.plan.{SourceSegmentRead,SharedSegmentRead,
// PlanRunRead,PlanHealthRead} field-for-field. Consumed by lib/planRunModel.ts
// to project a completed run onto per-node execution state (issue 08).

export interface SourceSegmentRead {
  node_id: string;
  source_id?: string | null;
  task_id?: string | null;
  run_id?: string | null;
  success: boolean;
  collected: number;
  stored: number;
  skipped: number;
  error?: string | null;
}

export interface SharedSegmentRead {
  run_key: string;
  success: boolean;
  failed_node_id?: string | null;
  error?: string | null;
  items_in: number;
  stored: number;
  skipped: number;
}

export interface PlanRunRead {
  plan_id: string;
  source_id: string;
  task_id: string;
  run_id?: string | null;
  success: boolean;
  collected: number;
  stored: number;
  skipped: number;
  error?: string | null;
  source_results: SourceSegmentRead[];
  shared_segment?: SharedSegmentRead | null;
}

export interface PlanHealthRead {
  id: string;
  plan_id: string;
  run_key: string;
  node_id: string;
  node_type: string;
  success: boolean;
  duration_ms: number;
  items_in: number;
  items_out: number;
  error_message?: string | null;
  detail: Record<string, unknown>;
  recorded_at: string;
}

// ── BrowserAct packs (Browser Act integration PR-E, decision #9) ──────────────────────────────
// Mirrors backend.api.v1.browser_act.BrowserActPackRead. Read-only vendored
// pack catalog for the 'browser_act' channel's config preset — never carries
// any credential/api_key (the BrowserAct API key is a SourceCredential,
// configured through the existing credential UI, not through pack config).
export interface BrowserActPack {
  name: string;
  description: string;
  category: string;
  domain: string;
  capability: string;
  path: string;
  has_manifest: boolean;
  param_schema: Array<{
    name: string;
    required: boolean;
    default?: string | null;
    enum?: string[] | null;
  }>;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface GovernedProjectSummary {
  id: string;
  workspace_id: string;
  name: string;
  slug: string;
  description: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export type SourceLifecycleStatus = "active" | "disabled" | "revoked";

export interface WorkspaceSource {
  id: string;
  workspace_id: string;
  name: string;
  slug: string;
  adapter_type: string;
  description: string | null;
  status: SourceLifecycleStatus;
  current_revision_number: number;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface SourceBinding {
  id: string;
  project_id: string;
  source_id: string;
  name: string;
  slug: string;
  status: SourceLifecycleStatus;
  current_revision_number: number;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface SourceBindingRevision {
  id: string;
  source_binding_id: string;
  revision_number: number;
  pinned_source_revision_id: string;
  scope_config: Record<string, unknown>;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface SourceBindingInput {
  source_id: string;
  name: string;
  slug: string;
  source_revision_number: number;
  scope_config: Record<string, unknown>;
}

export type ProjectAppType =
  "chatbot" | "agent" | "chatflow" | "workflow" | "text-generator";

export interface ProjectSummary {
  id: string;
  workspace_id: string;
  name: string;
  slug: string;
  description: string | null;
  app_type: ProjectAppType;
  primary_workflow_id: string | null;
  created_by_user_id: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export type RecordGraphNodeKind =
  "project" | "workflow" | "run" | "source" | "record" | "entity";
export type RecordGraphEdgeKind =
  | "contains"
  | "produced"
  | "origin"
  | "semantic"
  | "reference"
  | "batch"
  | "duplicate";

export interface RecordGraphNode {
  id: string;
  kind: RecordGraphNodeKind;
  label: string;
  subtitle: string | null;
  count: number;
  record_id: string | null;
  source_id: string | null;
  workflow_id: string | null;
  workflow_run_id: string | null;
  url: string | null;
  preview: string | null;
  status: string | null;
  source_published_at: string | null;
  created_at: string | null;
}

export interface RecordGraphEdge {
  id: string;
  source: string;
  target: string;
  kind: RecordGraphEdgeKind;
  label: string;
  weight: number;
  bidirectional: boolean;
}

export interface ProjectRecordGraphPreview {
  workspace_id: string;
  project_id: string;
  project_name: string;
  strategy: "server-aggregated-sample";
  truncated: boolean;
  max_nodes: number;
  nodes: RecordGraphNode[];
  edges: RecordGraphEdge[];
  stats: {
    total_records: number;
    sampled_records: number;
    hidden_records: number;
    total_sources: number;
    total_workflows: number;
    total_runs: number;
    visible_nodes: number;
    visible_edges: number;
  };
  generated_at: string;
}

export interface WorkflowAssetSummary {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  current_published_version: number | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectRuntimeLog {
  run_id: string;
  workflow_id: string;
  workflow_name: string;
  workflow_version: number | null;
  trace_id: string;
  status: import("@/lib/workflow/backend-runs").WorkflowRunStatus;
  trigger: string;
  response_mode: "async" | "sync-short-wait" | "callback";
  event_count: number;
  node_count: number;
  error_count: number;
  duration_ms: number;
  started_at: string;
  updated_at: string;
}

export interface ProjectRuntimeLogPage {
  logs: ProjectRuntimeLog[];
  meta: PaginationMeta;
}

export interface ProjectRuntimeTrace {
  workflow_version: number | null;
  inputs: Record<string, unknown>;
  user: string | null;
  response_mode: ProjectRuntimeLog["response_mode"];
  trace: import("@/lib/workflow/backend-runs").WorkflowRunTraceResponse;
}

export interface ProjectRuntimeSummary {
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  blocked_runs: number;
  running_runs: number;
  total_events: number;
  recent_logs: ProjectRuntimeLog[];
}

export interface WorkflowDraftRead {
  revision: number;
  graph: import("@/lib/workflow/schema").WorkflowProject;
  updated_by_user_id: string;
  updated_at: string;
}

export interface ProjectBootstrapResult {
  project: ProjectSummary;
  primary_workflow: WorkflowAssetSummary;
  draft: WorkflowDraftRead;
}

export interface WorkflowVersionSummary {
  id: string;
  workflow_id: string;
  version: number;
  draft_revision: number;
  graph: import("@/lib/workflow/schema").WorkflowProject;
  compile_version: string;
  published_by_user_id: string;
  reason: string;
  created_at: string;
}

export type OperationsWorkItemType =
  "incident" | "approval" | "change_proposal" | "review";
export type OperationsWorkItemStatus =
  "open" | "in_progress" | "resolved" | "closed" | "dismissed";

export interface OperationsWorkItem {
  id: string;
  workspace_id: string;
  type: OperationsWorkItemType;
  status: OperationsWorkItemStatus;
  severity: string;
  priority: string;
  owning_team_id: string | null;
  assignee_id: string | null;
  author_actor_type: string | null;
  author_actor_id: string | null;
  evidence: Record<string, unknown>;
  reason: string | null;
  parent_id: string | null;
  proposal_id: string | null;
  created_at: string;
  updated_at: string;
}

export type ApprovalDecision = "approve" | "reject" | "request_changes";

export interface ApprovalDecisionResult {
  approval: OperationsWorkItem;
  proposal: OperationsWorkItem;
  execution_state:
    | "awaiting_additional_approval"
    | "awaiting_actuator"
    | "changes_requested"
    | "denied";
}

export type OperationsAgentMode =
  "observe_only" | "suggest_changes" | "low_risk_automatic";

export interface OperationsAgentProfile {
  version: number;
  mode: OperationsAgentMode;
  tool_scope: string[];
  resource_scope: string[];
  action_scope: string[];
  assigned_by_user_id: string;
  reason: string;
  created_at: string;
}

export interface OperationsAgent {
  id: string;
  workspace_id: string;
  owning_team_id: string;
  name: string;
  description: string | null;
  disabled: boolean;
  current_published_version: number | null;
  current_profile: OperationsAgentProfile;
  effective_profile: OperationsAgentProfile | null;
  created_at: string;
  updated_at: string;
}

export interface AgentContractV1 {
  schema_version: "agent.contract.v1";
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  state_schema: Record<string, unknown>;
}

export interface AgentRuntimeBindingV2 {
  schema_version: "agent.runtime-binding.v2";
  workflow: string;
  preferred_agent_urls: string[];
  preferred_runtimes: Array<"miniflow" | "pi" | "codex">;
  model_binding: {
    schema_version: "agent.model-binding.v1";
    provider: string;
    model: string;
    auth_profile: string | null;
  } | null;
  config: Record<string, unknown>;
  dispatch_timeout_seconds: number;
}

export interface OperationsAgentDraft {
  revision: number;
  instructions: string;
  model_configuration: Record<string, unknown> & {
    agent_contract?: AgentContractV1;
    runtime_binding?: AgentRuntimeBindingV2;
  };
  tool_configuration: Record<string, unknown>;
  updated_by_user_id: string;
  updated_at: string;
}

export interface PublishedOperationsAgentVersion {
  version: number;
  draft_revision: number;
  instructions: string;
  model_configuration: OperationsAgentDraft["model_configuration"];
  tool_configuration: Record<string, unknown>;
  published_by_user_id: string;
  reason: string;
  created_at: string;
}

export interface OperationsAgentRun {
  id: string;
  workspace_id: string;
  operations_agent_id: string;
  published_version: number;
  profile_version: number;
  trigger_type: string;
  trigger_reference: string | null;
  target_resource_type: string;
  target_resource_id: string;
  input_payload: Record<string, unknown>;
  state_payload: Record<string, unknown>;
  output_payload: Record<string, unknown> | null;
  error_message: string | null;
  status:
    "queued" | "running" | "paused" | "completed" | "failed" | "cancelled";
  started_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface Automation {
  id: string;
  workspace_id: string;
  name: string;
  prompt: string;
  precheck: string | null;
  executor: string;
  schedule: string;
  timezone: string;
  session_mode: "fresh" | "reuse";
  approval_mode: OperationsAgentMode;
  project: Record<string, unknown>;
  enabled: boolean;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface WorkbenchRepository {
  id: string
  name: string
  defaultRef: string
}

export interface WorkbenchRuntime {
  id: string
  name: string
  publishedVersion: number
  runtimeType: string
  readiness: 'ready' | 'blocked'
  reasonCode: string | null
  reason: string | null
}

export interface WorkbenchTestEvidence {
  command: string
  outcome: 'passed' | 'failed' | 'unknown'
  summary: string
}

export interface WorkbenchProposal {
  id: string
  status: 'pending_confirmation' | 'applied' | 'failed' | 'cancelled'
  baseSha: string
  checkpointSha: string
  diff: string
  modifiedFiles: string[]
  tests: WorkbenchTestEvidence[]
  errorMessage: string | null
  confirmedAt: string | null
}

export interface WorkbenchTurn {
  id: string
  sequence: number
  requestId: string
  requirement: string
  runtimeId: string
  publishedVersion: number
  runtimeType: string
  status: 'queued' | 'running' | 'proposed' | 'applied' | 'failed' | 'cancelled'
  baseSha: string
  output: {
    modifiedFiles: string[]
    tests: WorkbenchTestEvidence[]
    diff: string
    proposal: WorkbenchProposal | null
  } | null
  errorMessage: string | null
  createdAt: string
  updatedAt: string
}

export interface WorkbenchThread {
  id: string
  repositoryId: string
  title: string | null
  status: 'active' | 'closed'
  createdAt: string
  updatedAt: string
  turns: WorkbenchTurn[]
}

export interface WorkbenchEvent {
  id: string
  sequence: number
  eventType: 'started' | 'text' | 'tool_call' | 'tool_result' | 'state' | 'proposal' | 'done' | 'error' | 'cancelled'
  payload: Record<string, unknown>
  createdAt: string
}

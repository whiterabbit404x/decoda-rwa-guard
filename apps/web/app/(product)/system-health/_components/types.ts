export type ComponentStatus = 'healthy' | 'degraded' | 'failing' | 'unavailable';

export type ComponentDetail = {
  status: ComponentStatus;
  message: string;
  age?: string | null;
  last_event?: string | null;
  metric?: string | null;
  action?: string | null;
};

export type RealtimeIngestionPath = {
  status: 'active' | 'degraded' | 'disabled' | 'rate_limited';
  enabled: boolean;
  ws_configured: boolean;
  provider_mode: string;
  last_event_at: string | null;
  events_processed: number | null;
  reconnect_count: number | null;
  watcher_name: string | null;
  heartbeat_age_seconds?: number | null;
  degraded_reason?: string | null;
  rate_limited?: boolean;
  provider?: string | null;
  next_retry_at?: string | null;
  stable_polling_active?: boolean;
  // Worker-heartbeat fallback facts: fallback_active=true means the WSS is degraded
  // past its breaker thresholds and detection handed off to the path named by
  // worker_provider_mode (quicknode_http_fast_tail / stable_rpc_polling_fallback).
  fallback_active?: boolean;
  worker_provider_mode?: string | null;
  label: string;
};

export type LiveChainMonitoring = {
  expected_chain_id: number;
  rpc_configured: boolean;
  latest_rpc_block: string | null;
  worker_enabled: boolean;
  worker_enabled_source?: string | null;
  last_heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  heartbeat_age_human: string | null;
  polling_interval_seconds: number;
  last_poll_at: string | null;
  last_successful_poll_at: string | null;
  latest_polled_block: number | null;
  last_telemetry_at: string | null;
  last_detection_at: string | null;
  recent_telemetry_1h: number;
  recent_telemetry_24h: number;
  recent_detections_1h: number;
  recent_detections_24h: number;
  diagnosis: string;
  realtime_ingestion_path?: RealtimeIngestionPath | null;
};

export type HealthEvent = {
  time: string;
  component: string;
  event: string;
  severity: string;
  kind?: string;
};

export type ProviderEntry = {
  name: string;
  type: string;
  status: string;
  message: string;
  last_event?: string | null;
  metric?: string | null;
  action?: string | null;
};

export type SystemHealthPayload = {
  generated_at: string;
  environment: string;
  version: string | null;
  git_commit: string | null;
  overall_status: 'healthy' | 'degraded' | 'failing' | 'unavailable';
  summary: string;
  primary_action: string | null;
  components: Record<string, ComponentDetail>;
  live_chain_monitoring: LiveChainMonitoring;
  events: HealthEvent[];
  providers: ProviderEntry[];
  reliability: Record<string, string | number | null>;
};

export const COMPONENT_META: Record<string, { label: string; what: string }> = {
  api: { label: 'API', what: 'HTTP endpoint reachability' },
  database: { label: 'Database', what: 'SELECT 1 query' },
  redis: { label: 'Redis', what: 'PING connectivity' },
  worker: { label: 'Worker', what: 'Heartbeat freshness' },
  base_rpc: { label: 'Base RPC', what: 'eth_blockNumber call' },
  live_polling: { label: 'Live Polling', what: 'Last monitoring poll time' },
  telemetry: { label: 'Telemetry Ingestion', what: 'Last telemetry event age' },
  detection: { label: 'Detection', what: 'Last wallet_transfer_detected' },
  alert_delivery: { label: 'Alert Delivery', what: 'Outbox + stream health' },
  realtime_path: { label: 'Realtime Ingestion', what: 'Base WebSocket real-time event path' },
};

export const COMPONENT_ORDER = [
  'api',
  'database',
  'redis',
  'worker',
  'base_rpc',
  'telemetry',
  'detection',
  'alert_delivery',
] as const;

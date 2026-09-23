/** Shapes the External Watchlist API returns (founder console only). */
import type { BackfillSummary, NetworkRef, ProspectReport, WatchlistSummary } from './external-watchlist-view';

export type TargetBackfill = {
  id: string;
  target_id: string;
  status: string;
  requested_days: number;
  percent: number;
  days_analyzed: number;
  scanned_blocks: number;
  total_blocks: number;
  planned: boolean;
  events_inserted: number;
  status_reason: string | null;
  last_error: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export type WatchlistTarget = {
  id: string;
  watchlist_id: string;
  target_type: string;
  network: NetworkRef;
  chain_id: number;
  address: string;
  label: string | null;
  contract_type: string | null;
  has_code: boolean | null;
  monitoring_enabled: boolean;
  monitoring_scope: string;
  execution_authority: string;
  live_start_block: number | null;
  last_processed_block: number | null;
  last_polled_at: string | null;
  last_successful_poll_at: string | null;
  last_event_at: string | null;
  last_poll_error: string | null;
  consecutive_failures: number;
  explorer_url: string | null;
  backfill: TargetBackfill | null;
};

export type RpcHealth = {
  network: string;
  network_label: string;
  state: string;
  last_error: string | null;
  last_processed_block: number | null;
};

export type RecentActivity = {
  event_name: string;
  event_category: string;
  contract_address: string;
  tx_hash: string;
  tx_explorer_url: string | null;
  block_number: number;
  observed_at: string | null;
};

export type WatchlistDetail = {
  watchlist: WatchlistSummary;
  overview: {
    status: string;
    status_reason: string | null;
    status_reason_label: string | null;
    contracts_monitored: number;
    wallets_monitored: number;
    events_24h: number;
    events_total: number;
    findings_30d: number;
    findings_new: number;
    last_block_processed: Array<{ network: NetworkRef; block: number | null }>;
    rpc_health: RpcHealth[];
    last_successful_poll_at: string | null;
    worker_heartbeat_at: string | null;
    backfill: BackfillSummary;
    recent_activity: RecentActivity[];
  };
  targets: WatchlistTarget[];
  conversion: {
    workspace_id: string | null;
    organization_id: string | null;
    converted_at: string | null;
    evaluation_days: number;
    evaluation_expires_at: string | null;
    copied_targets: Array<Record<string, unknown>>;
    customer_authorized: boolean;
  } | null;
  notices: Record<string, string>;
};

export type ObservedEvent = {
  id: string;
  target_id: string;
  target_label: string | null;
  target_address: string | null;
  event_name: string;
  event_category: string;
  network: NetworkRef;
  contract_address: string;
  tx_hash: string;
  tx_explorer_url: string | null;
  block_number: number;
  log_index: number;
  decoded: Record<string, unknown>;
  decode_status: string;
  initiator: string | null;
  observed_at: string | null;
  observed_at_source: string;
  ingest_source: string;
  payload_sha256: string;
};

export type Finding = {
  id: string;
  target_id: string | null;
  target_label: string | null;
  target_address: string | null;
  target_type: string | null;
  title: string;
  finding_type: string;
  finding_class: string;
  finding_class_label: string | null;
  severity: string;
  status: string;
  status_label: string | null;
  network: NetworkRef;
  contract_address: string | null;
  tx_hash: string | null;
  tx_explorer_url: string | null;
  contract_explorer_url: string | null;
  block_number: number | null;
  observed_at: string | null;
  initiator: string | null;
  detected_at: string | null;
  source_label: string;
  execution_authority: string;
  explanation?: string;
  decoded?: Record<string, unknown>;
  previous_state?: Record<string, unknown> | null;
  new_state?: Record<string, unknown> | null;
  ai_analysis?: { observed_fact?: string; decoda_interpretation?: string; operational_authorization?: string; source?: string };
  status_note?: string | null;
};

export type EvidenceVerification = {
  status: string;
  valid: boolean;
  errors: string[];
  manifest_sha256?: string;
  evidence_sha256?: string;
  signature_algorithm?: string;
  public_key_signature?: string;
  production_secret?: boolean;
  warning?: string | null;
};

export type EvidenceRow = {
  id: string;
  finding_id: string | null;
  finding_title?: string | null;
  finding_tx_hash?: string | null;
  package_type: string;
  source_evidence_id: string | null;
  manifest_sha256: string;
  evidence_sha256: string;
  signature_algorithm: string | null;
  generated_at: string | null;
  verification?: EvidenceVerification | null;
};

export type FindingDetailPayload = {
  finding: Finding;
  protocol: { id: string; name: string; website_url: string | null };
  related_telemetry: ObservedEvent[];
  evidence: EvidenceRow[];
  evidence_integrity: EvidenceVerification | null;
};

export type { ProspectReport };

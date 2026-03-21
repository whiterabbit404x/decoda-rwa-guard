export type HistoryWorkspace = { id: string; name: string; slug: string };

export type HistoryPayload = {
  mode: string;
  workspace: HistoryWorkspace;
  role: 'workspace_owner' | 'workspace_admin' | 'workspace_member';
  counts: {
    analysis_runs: number;
    alerts: number;
    governance_actions: number;
    incidents: number;
    audit_logs: number;
  };
  analysis_runs: Array<{
    id: string;
    analysis_type: string;
    service_name: string;
    status: string;
    title: string;
    source: string;
    summary: string;
    request_payload: Record<string, unknown>;
    response_payload: Record<string, unknown>;
    created_at: string;
  }>;
  alerts: Array<{
    id: string;
    alert_type: string;
    title: string;
    severity: string;
    status: string;
    source_service: string;
    summary: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
  governance_actions: Array<{
    id: string;
    action_type: string;
    target_type: string;
    target_id: string;
    status: string;
    reason: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
  incidents: Array<{
    id: string;
    event_type: string;
    severity: string;
    status: string;
    summary: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
  audit_logs: Array<{
    id: string;
    action: string;
    entity_type: string;
    entity_id: string;
    ip_address: string | null;
    metadata: Record<string, unknown>;
    created_at: string;
  }>;
};

export function filterRecordsByRecentActivity<T extends { created_at: string }>(items: T[], days: number) {
  if (!Number.isFinite(days) || days <= 0) {
    return items;
  }
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return items.filter((item) => new Date(item.created_at).getTime() >= cutoff);
}

export function determineHistoryCategory(analysisType: string) {
  if (analysisType.startsWith('threat_')) {
    return 'threat';
  }
  if (analysisType.startsWith('compliance_') || analysisType === 'governance_action') {
    return 'compliance';
  }
  if (analysisType.startsWith('resilience_')) {
    return 'resilience';
  }
  return 'other';
}

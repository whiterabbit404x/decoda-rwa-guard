// Data layer for Screen 2 (Dashboard / Executive Summary).
//
// Types mirror the backend `/ops/dashboard/executive-summary` contract, plus a
// defensive normalizer that tolerates partial/degraded payloads so the UI never
// throws on a missing field. All numbers originate from real workspace data on
// the backend — nothing here fabricates metrics, and a null asset valuation is
// preserved as `null` (rendered "Not available"), never coerced to 0.

export type RiskBand = 'low' | 'moderate' | 'high' | 'critical';
export type HealthStatus = 'healthy' | 'degraded' | 'at_risk' | 'critical' | 'not_configured';
export type GenerationMode = 'ai' | 'deterministic_fallback';
export type FreshnessStatus = 'fresh' | 'stale' | 'unavailable';

export type Citation = {
  source_type: string;
  source_id: string;
  label: string;
  occurred_at: string | null;
  url: string;
};

export type KeyFinding = {
  title: string;
  description: string;
  severity: string;
  source_refs: Citation[];
};

export type RecommendedFocus = {
  title: string;
  reason: string;
  destination: string;
};

export type ExecutiveBrief = {
  period_start: string | null;
  period_end: string | null;
  headline: string;
  summary: string;
  key_findings: KeyFinding[];
  recommended_focus: RecommendedFocus[];
  confidence: number;
  generation_mode: GenerationMode;
  generated_at: string | null;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  citations: Citation[];
};

export type MetricDeltas = {
  risk_score: number | null;
  system_health_score: number | null;
  active_alert_count: number | null;
  open_incident_count: number | null;
};

export type ExecMetrics = {
  total_asset_value_usd: number | null;
  monitored_asset_count: number;
  active_monitor_count: number;
  data_source_count: number;
  open_incident_count: number;
  active_alert_count: number;
  risk_score: number;
  risk_band: RiskBand;
  system_health_score: number;
  system_health_status: HealthStatus;
  uptime_30d_percent: number | null;
  critical_or_high_incident_count: number;
  deltas: MetricDeltas;
};

export type RiskTrendPoint = {
  captured_at: string | null;
  risk_score: number;
  health_score: number;
  active_alert_count: number;
  open_incident_count: number;
};

export type RecentAlert = {
  id: string;
  title: string;
  severity: string;
  status: string;
  asset: string;
  occurred_at: string | null;
  url: string;
};

export type RiskDriver = {
  key: string;
  label: string;
  points: number;
  percent: number;
  detail: string;
};

export type HealthInsight = {
  severity: string;
  message: string;
  source_type: string;
  source_id: string;
  occurred_at: string | null;
};

export type AiCopilot = {
  generated_at: string | null;
  top_risk_drivers: RiskDriver[];
  system_health_insights: HealthInsight[];
  recommended_focus: RecommendedFocus[];
  generation_mode: GenerationMode;
};

export type DataFreshness = {
  status: FreshnessStatus;
  latest_event_at: string | null;
  age_seconds: number | null;
};

export type ExecutiveSummary = {
  generated_at: string | null;
  data_freshness: DataFreshness;
  executive_brief: ExecutiveBrief;
  metrics: ExecMetrics;
  risk_trend: RiskTrendPoint[];
  trend_available: boolean;
  recent_alerts: RecentAlert[];
  ai_copilot: AiCopilot;
};

// -- safe coercers -----------------------------------------------------------

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}
function str(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
}
function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}
function numOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function riskBand(value: unknown): RiskBand {
  const v = str(value).toLowerCase();
  return v === 'moderate' || v === 'high' || v === 'critical' ? v : 'low';
}
function healthStatus(value: unknown): HealthStatus {
  const v = str(value).toLowerCase();
  return v === 'healthy' || v === 'degraded' || v === 'at_risk' || v === 'critical' ? v : 'not_configured';
}
function generationMode(value: unknown): GenerationMode {
  return str(value) === 'ai' ? 'ai' : 'deterministic_fallback';
}
function freshnessStatus(value: unknown): FreshnessStatus {
  const v = str(value).toLowerCase();
  return v === 'fresh' || v === 'stale' ? v : 'unavailable';
}

function mapCitation(value: unknown): Citation {
  const c = rec(value);
  return {
    source_type: str(c.source_type),
    source_id: str(c.source_id),
    label: str(c.label),
    occurred_at: c.occurred_at == null ? null : str(c.occurred_at),
    url: str(c.url),
  };
}

function mapFocus(value: unknown): RecommendedFocus {
  const f = rec(value);
  return { title: str(f.title), reason: str(f.reason), destination: str(f.destination, 'monitoring') };
}

export function mapExecutiveSummary(raw: unknown): ExecutiveSummary {
  const root = rec(raw);
  const metrics = rec(root.metrics);
  const deltas = rec(metrics.deltas);
  const brief = rec(root.executive_brief);
  const copilot = rec(root.ai_copilot);
  const freshness = rec(root.data_freshness);

  return {
    generated_at: root.generated_at == null ? null : str(root.generated_at),
    data_freshness: {
      status: freshnessStatus(freshness.status),
      latest_event_at: freshness.latest_event_at == null ? null : str(freshness.latest_event_at),
      age_seconds: numOrNull(freshness.age_seconds),
    },
    executive_brief: {
      period_start: brief.period_start == null ? null : str(brief.period_start),
      period_end: brief.period_end == null ? null : str(brief.period_end),
      headline: str(brief.headline),
      summary: str(brief.summary),
      key_findings: arr(brief.key_findings).map((f) => {
        const finding = rec(f);
        return {
          title: str(finding.title),
          description: str(finding.description),
          severity: str(finding.severity, 'medium'),
          source_refs: arr(finding.source_refs).map(mapCitation),
        };
      }),
      recommended_focus: arr(brief.recommended_focus).map(mapFocus),
      confidence: num(brief.confidence),
      generation_mode: generationMode(brief.generation_mode),
      generated_at: brief.generated_at == null ? null : str(brief.generated_at),
      provider: brief.provider == null ? null : str(brief.provider),
      model: brief.model == null ? null : str(brief.model),
      prompt_version: brief.prompt_version == null ? null : str(brief.prompt_version),
      citations: arr(brief.citations).map(mapCitation),
    },
    metrics: {
      total_asset_value_usd: numOrNull(metrics.total_asset_value_usd),
      monitored_asset_count: num(metrics.monitored_asset_count),
      active_monitor_count: num(metrics.active_monitor_count),
      data_source_count: num(metrics.data_source_count),
      open_incident_count: num(metrics.open_incident_count),
      active_alert_count: num(metrics.active_alert_count),
      risk_score: num(metrics.risk_score),
      risk_band: riskBand(metrics.risk_band),
      system_health_score: num(metrics.system_health_score),
      system_health_status: healthStatus(metrics.system_health_status),
      uptime_30d_percent: numOrNull(metrics.uptime_30d_percent),
      critical_or_high_incident_count: num(metrics.critical_or_high_incident_count),
      deltas: {
        risk_score: numOrNull(deltas.risk_score),
        system_health_score: numOrNull(deltas.system_health_score),
        active_alert_count: numOrNull(deltas.active_alert_count),
        open_incident_count: numOrNull(deltas.open_incident_count),
      },
    },
    risk_trend: arr(root.risk_trend).map((p) => {
      const point = rec(p);
      return {
        captured_at: point.captured_at == null ? null : str(point.captured_at),
        risk_score: num(point.risk_score),
        health_score: num(point.health_score),
        active_alert_count: num(point.active_alert_count),
        open_incident_count: num(point.open_incident_count),
      };
    }),
    trend_available: Boolean(root.trend_available) && arr(root.risk_trend).length > 0,
    recent_alerts: arr(root.recent_alerts).map((a) => {
      const alert = rec(a);
      return {
        id: str(alert.id),
        title: str(alert.title, 'Alert'),
        severity: str(alert.severity, 'medium'),
        status: str(alert.status, 'open'),
        asset: str(alert.asset),
        occurred_at: alert.occurred_at == null ? null : str(alert.occurred_at),
        url: str(alert.url, alert.id ? `/alerts/${str(alert.id)}` : '/alerts'),
      };
    }),
    ai_copilot: {
      generated_at: copilot.generated_at == null ? null : str(copilot.generated_at),
      top_risk_drivers: arr(copilot.top_risk_drivers).map((d) => {
        const driver = rec(d);
        return {
          key: str(driver.key),
          label: str(driver.label),
          points: num(driver.points),
          percent: num(driver.percent),
          detail: str(driver.detail),
        };
      }),
      system_health_insights: arr(copilot.system_health_insights).map((i) => {
        const insight = rec(i);
        return {
          severity: str(insight.severity, 'info'),
          message: str(insight.message),
          source_type: str(insight.source_type),
          source_id: str(insight.source_id),
          occurred_at: insight.occurred_at == null ? null : str(insight.occurred_at),
        };
      }),
      recommended_focus: arr(copilot.recommended_focus).map(mapFocus),
      generation_mode: generationMode(copilot.generation_mode),
    },
  };
}

// -- presentation helpers ----------------------------------------------------

/** USD compact notation (e.g. $3.42B). Returns "Not available" for null — a
 *  missing valuation must never render as $0. */
export function formatAssetValue(value: number | null): string {
  if (value == null) return 'Not available';
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      notation: 'compact',
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `$${value.toLocaleString('en-US')}`;
  }
}

export const RISK_BAND_LABELS: Record<RiskBand, string> = {
  low: 'Low',
  moderate: 'Moderate',
  high: 'High',
  critical: 'Critical',
};

export const HEALTH_STATUS_LABELS: Record<HealthStatus, string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  at_risk: 'At Risk',
  critical: 'Critical',
  not_configured: 'Not configured',
};

export function riskBandVariant(band: RiskBand): 'success' | 'warning' | 'danger' {
  if (band === 'critical' || band === 'high') return 'danger';
  if (band === 'moderate') return 'warning';
  return 'success';
}

export function healthStatusVariant(status: HealthStatus): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'healthy') return 'success';
  if (status === 'degraded') return 'warning';
  if (status === 'at_risk' || status === 'critical') return 'danger';
  return 'neutral';
}

/** Signed delta label. `null` => no prior snapshot => empty string (caller hides). */
export function formatDelta(delta: number | null, opts: { invertGood?: boolean } = {}): { text: string; tone: 'up' | 'down' | 'flat' } {
  if (delta == null) return { text: '', tone: 'flat' };
  if (delta === 0) return { text: '±0 (7d)', tone: 'flat' };
  const sign = delta > 0 ? '+' : '−';
  return { text: `${sign}${Math.abs(delta)} (7d)`, tone: delta > 0 ? 'up' : 'down' };
}

export function formatRelativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return 'unknown';
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return 'unknown';
  const seconds = Math.max(0, Math.round((now - ts) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export const EXECUTIVE_SUMMARY_ENDPOINT = '/api/dashboard/executive-summary';

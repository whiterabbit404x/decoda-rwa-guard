import { expect, test } from '@playwright/test';

import {
  connectionStatusFromStream,
  formatAgeSeconds,
  formatAssetValue,
  formatDelta,
  formatRelativeTime,
  healthStatusVariant,
  mapExecutiveSummary,
  monitoringStateVariant,
  riskBandVariant,
  CONNECTION_STATUS_LABELS,
  DATA_CONFIDENCE_LABELS,
  HEALTH_STATUS_LABELS,
  MONITORING_STATUS_LABELS,
  RISK_BAND_LABELS,
} from '../app/dashboard-executive-summary-data';

function sampleRaw(overrides: Record<string, unknown> = {}) {
  return {
    generated_at: '2026-07-23T12:00:00Z',
    data_freshness: { status: 'fresh', latest_event_at: '2026-07-23T11:58:00Z', age_seconds: 120 },
    executive_brief: {
      period_start: '2026-07-22T12:00:00Z',
      period_end: '2026-07-23T12:00:00Z',
      headline: 'One open incident requires attention',
      summary: 'Risk 42/100 moderate. Health degraded.',
      key_findings: [
        { title: 'Oracle deviation', description: 'Alert fired', severity: 'high', source_refs: [{ source_type: 'alert', source_id: 'a1', label: 'Oracle deviation', occurred_at: null, url: '/alerts/a1' }] },
      ],
      recommended_focus: [{ title: 'Triage alerts', reason: 'Active alerts', destination: 'alerts' }],
      confidence: 0.8,
      generation_mode: 'ai',
      generated_at: '2026-07-23T12:00:00Z',
      provider: 'openai',
      model: 'gpt-x',
      prompt_version: 'dashboard-brief-2026-07-1',
      citations: [{ source_type: 'alert', source_id: 'a1', label: 'Oracle deviation', occurred_at: null, url: '/alerts/a1' }],
    },
    metrics: {
      total_asset_value_usd: null,
      monitored_asset_count: 5,
      active_monitor_count: 4,
      data_source_count: 3,
      open_incident_count: 1,
      active_alert_count: 3,
      risk_score: 42,
      risk_band: 'moderate',
      system_health_score: 88,
      system_health_status: 'degraded',
      uptime_30d_percent: 99.97,
      critical_or_high_incident_count: 1,
      deltas: { risk_score: 5, system_health_score: -3, active_alert_count: 2, open_incident_count: 0 },
    },
    risk_trend: [
      { captured_at: '2026-07-22T12:00:00Z', risk_score: 30, health_score: 95, active_alert_count: 1, open_incident_count: 0 },
      { captured_at: '2026-07-23T12:00:00Z', risk_score: 42, health_score: 88, active_alert_count: 3, open_incident_count: 1 },
    ],
    trend_available: true,
    recent_alerts: [
      { id: 'a1', title: 'Oracle deviation', severity: 'high', status: 'open', asset: 'oracle', occurred_at: '2026-07-23T10:00:00Z', url: '/alerts/a1' },
    ],
    ai_copilot: {
      generated_at: '2026-07-23T12:00:00Z',
      top_risk_drivers: [{ key: 'alert_pressure', label: 'Active alert severity & volume', points: 12.5, percent: 55, detail: '3 clusters' }],
      system_health_insights: [{ severity: 'warning', message: 'Telemetry is stale.', source_type: 'monitoring_target', source_id: 't1', occurred_at: '2026-07-23T10:00:00Z' }],
      recommended_focus: [{ title: 'Triage alerts', reason: 'Active alerts', destination: 'alerts' }],
      generation_mode: 'ai',
    },
    ...overrides,
  };
}

test.describe('executive summary data layer', () => {
  // Required frontend test 2: API metrics render correctly (map is faithful).
  test('maps a full payload faithfully', () => {
    const data = mapExecutiveSummary(sampleRaw());
    expect(data.metrics.active_alert_count).toBe(3);
    expect(data.metrics.open_incident_count).toBe(1);
    expect(data.metrics.risk_score).toBe(42);
    expect(data.metrics.risk_band).toBe('moderate');
    expect(data.metrics.system_health_score).toBe(88);
    expect(data.metrics.system_health_status).toBe('degraded');
    expect(data.metrics.data_source_count).toBe(3);
    expect(data.risk_trend).toHaveLength(2);
    expect(data.recent_alerts[0].url).toBe('/alerts/a1');
    expect(data.ai_copilot.top_risk_drivers[0].percent).toBe(55);
  });

  // Required frontend test 3: null valuation renders "Not available" (not $0).
  test('null asset valuation is "Not available", not $0', () => {
    const data = mapExecutiveSummary(sampleRaw());
    expect(data.metrics.total_asset_value_usd).toBeNull();
    expect(formatAssetValue(data.metrics.total_asset_value_usd)).toBe('Not available');
    expect(formatAssetValue(0)).not.toBe('Not available');
    expect(formatAssetValue(3_420_000_000)).toContain('B');
  });

  // Required frontend test 5: risk and health states use the correct labels.
  test('risk and health labels + variants are correct', () => {
    expect(RISK_BAND_LABELS.moderate).toBe('Moderate');
    expect(RISK_BAND_LABELS.critical).toBe('Critical');
    expect(HEALTH_STATUS_LABELS.healthy).toBe('Healthy');
    expect(HEALTH_STATUS_LABELS.at_risk).toBe('At Risk');
    expect(HEALTH_STATUS_LABELS.not_configured).toBe('Not configured');
    expect(riskBandVariant('critical')).toBe('danger');
    expect(riskBandVariant('low')).toBe('success');
    expect(healthStatusVariant('healthy')).toBe('success');
    expect(healthStatusVariant('degraded')).toBe('warning');
    expect(healthStatusVariant('not_configured')).toBe('neutral');
  });

  // Truthfulness: not_configured is never labeled Healthy.
  test('not_configured health is never labeled Healthy', () => {
    const data = mapExecutiveSummary(sampleRaw({ metrics: { ...sampleRaw().metrics, system_health_status: 'not_configured' } }));
    expect(HEALTH_STATUS_LABELS[data.metrics.system_health_status]).toBe('Not configured');
    expect(HEALTH_STATUS_LABELS[data.metrics.system_health_status]).not.toBe('Healthy');
  });

  test('degraded payload preserves partial data defensively', () => {
    const data = mapExecutiveSummary({ metrics: { active_alert_count: 2 } });
    expect(data.metrics.active_alert_count).toBe(2);
    // Missing fields degrade safely, never throw.
    expect(data.metrics.total_asset_value_usd).toBeNull();
    expect(data.metrics.risk_band).toBe('low');
    expect(data.metrics.system_health_status).toBe('not_configured');
    expect(data.risk_trend).toEqual([]);
    expect(data.trend_available).toBe(false);
    expect(data.recent_alerts).toEqual([]);
  });

  test('map tolerates completely empty / garbage input', () => {
    expect(() => mapExecutiveSummary(null)).not.toThrow();
    expect(() => mapExecutiveSummary('nonsense')).not.toThrow();
    const data = mapExecutiveSummary(undefined);
    expect(data.metrics.monitored_asset_count).toBe(0);
    expect(data.executive_brief.generation_mode).toBe('deterministic_fallback');
  });

  test('delta formatting shows signed 7-day change with tone', () => {
    expect(formatDelta(5)).toEqual({ text: '+5 (7d)', tone: 'up' });
    expect(formatDelta(-3).tone).toBe('down');
    expect(formatDelta(0)).toEqual({ text: '±0 (7d)', tone: 'flat' });
    // No prior snapshot -> empty text (caller hides).
    expect(formatDelta(null)).toEqual({ text: '', tone: 'flat' });
  });

  test('relative time formatting', () => {
    const now = Date.parse('2026-07-23T12:00:00Z');
    expect(formatRelativeTime('2026-07-23T11:59:30Z', now)).toBe('30s ago');
    expect(formatRelativeTime('2026-07-23T11:30:00Z', now)).toBe('30m ago');
    expect(formatRelativeTime('2026-07-23T09:00:00Z', now)).toBe('3h ago');
    expect(formatRelativeTime(null, now)).toBe('unknown');
  });

  // Required frontend test 6: alert rows navigate correctly (url mapping).
  test('recent alerts always carry a navigable url', () => {
    const data = mapExecutiveSummary(sampleRaw({ recent_alerts: [{ id: 'zzz', title: 'x', severity: 'low', status: 'open' }] }));
    expect(data.recent_alerts[0].url).toBe('/alerts/zzz');
  });

  // Monitoring status is a separate axis from the SSE transport.
  test('healthy monitoring state maps to Live monitoring', () => {
    const data = mapExecutiveSummary(sampleRaw({
      monitoring_state: { state: 'live', label: 'Live monitoring', reason: 'ok', telemetry_fresh: true, workers_fresh: true, ingestion_healthy: true },
    }));
    expect(data.monitoring_state.state).toBe('live');
    expect(MONITORING_STATUS_LABELS[data.monitoring_state.state]).toBe('Live monitoring');
    expect(monitoringStateVariant('live')).toBe('success');
  });

  test('degraded monitoring state never maps to Live monitoring', () => {
    const data = mapExecutiveSummary(sampleRaw({
      monitoring_state: { state: 'degraded', label: 'Monitoring degraded', reason: 'stale', telemetry_fresh: false, workers_fresh: true, ingestion_healthy: true },
    }));
    expect(data.monitoring_state.state).toBe('degraded');
    expect(MONITORING_STATUS_LABELS[data.monitoring_state.state]).not.toBe('Live monitoring');
    expect(MONITORING_STATUS_LABELS[data.monitoring_state.state]).toBe('Monitoring degraded');
  });

  // Fail closed: an absent monitoring_state must never read as "Live monitoring".
  test('absent monitoring state fails closed to offline (never live)', () => {
    const data = mapExecutiveSummary(sampleRaw());
    expect(data.monitoring_state.state).toBe('offline');
    expect(MONITORING_STATUS_LABELS[data.monitoring_state.state]).not.toBe('Live monitoring');
  });

  // SSE transport is mapped independently — 'live' stream = "Connected", not "Live monitoring".
  test('SSE stream status maps to a distinct connection label', () => {
    expect(connectionStatusFromStream('live')).toBe('connected');
    expect(CONNECTION_STATUS_LABELS.connected).toBe('Connected');
    expect(connectionStatusFromStream('reconnecting')).toBe('reconnecting');
    expect(connectionStatusFromStream('polling-fallback')).toBe('reconnecting');
    expect(connectionStatusFromStream('disconnected')).toBe('disconnected');
    // Crucially: "Connected" transport is never the string "Live monitoring".
    expect(CONNECTION_STATUS_LABELS.connected).not.toBe('Live monitoring');
  });

  // Evidence freshness: generation time and data-current-through are distinct.
  test('evidence block maps generation time separately from data freshness', () => {
    const data = mapExecutiveSummary(sampleRaw({
      executive_brief: {
        ...sampleRaw().executive_brief,
        evidence: {
          generated_at: '2026-07-23T12:00:00Z',
          data_current_through: '2026-07-22T17:00:00Z',
          telemetry_age_seconds: 68400,
          telemetry_status: 'stale',
          data_confidence: 'low',
          data_confidence_reason: 'Telemetry is stale.',
          generation_mode: 'deterministic_fallback',
        },
      },
    }));
    const ev = data.executive_brief.evidence;
    expect(ev.generated_at).toBe('2026-07-23T12:00:00Z');
    expect(ev.data_current_through).toBe('2026-07-22T17:00:00Z');
    expect(ev.telemetry_age_seconds).toBe(68400);
    // Low confidence renders when evidence is stale; never silently "Medium".
    expect(ev.data_confidence).toBe('low');
    expect(DATA_CONFIDENCE_LABELS[ev.data_confidence]).toBe('Low');
    expect(DATA_CONFIDENCE_LABELS[ev.data_confidence]).not.toBe('Medium');
  });

  test('evidence generated_at falls back to brief generated_at when absent', () => {
    const data = mapExecutiveSummary(sampleRaw());
    // sampleRaw has no evidence block; generation time still resolves.
    expect(data.executive_brief.evidence.generated_at).toBe('2026-07-23T12:00:00Z');
    expect(data.executive_brief.evidence.data_confidence).toBe('unavailable');
  });

  test('telemetry age formats compactly (19h stale)', () => {
    expect(formatAgeSeconds(68400)).toBe('19h');
    expect(formatAgeSeconds(45)).toBe('45s');
    expect(formatAgeSeconds(null)).toBe('unknown');
  });
});

/**
 * Threat Monitoring — top banner + quiet-wallet telemetry copy.
 *
 * Production evidence this locks:
 *   chosen_evidence_source=live, realtime_ingestion_status=healthy,
 *   realtime_ingestion_healthy=True, realtime_live_evidence_fresh=True,
 *   realtime_live_coverage_fresh=True, fallback_rpc_degraded=False,
 *   status_reason=None, monitoring_status=idle (decision=idle),
 *   QuickNode Stream lag_blocks=0-1 / lag_status=live / health_status=healthy
 *
 * ...while the UI still showed "LIMITED COVERAGE" plus an obsolete
 * "Limitation: Runtime condition: guard:incident exists without alert", and the
 * page claimed "Telemetry is stale — detections may be based on older data."
 *
 * Two frontend causes, both fixed here:
 *   1. `idle` was normalized to `limited` unconditionally, and the frontend's own
 *      `idle_runtime_with_active_monitoring_claim` guard then overrode the
 *      backend's null status_reason with a manufactured `guard:` reason.
 *   2. The Threat summary's data_freshness measures SECURITY-EVENT recency only.
 *      On a quiet wallet that is legitimately old while live coverage is current.
 *
 * Fail-closed stays the rule: limited / degraded / offline / catching-up Stream
 * all keep their warning, and idle without a proven realtime verdict (or with any
 * reported status reason) is still limited.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import type {
  MonitoringRuntimeStatus,
  RealtimeIngestionFactsPayload,
  WorkerStatusSummary,
} from '../app/monitoring-status-contract';
import {
  compactReason,
  deriveBannerState,
  stateLabel,
  workerStatusBannerLine,
} from '../app/workspace-monitoring-mode-banner';
import {
  quietMonitoringNote,
  resolveWorkspaceMonitoringTruth,
} from '../app/workspace-monitoring-truth';
import { runtimeReasonMessage } from '../app/runtime-reason-copy';
import {
  QUIET_LIVE_COVERAGE_COPY,
  coverageIncompleteWarningApplies,
  coverageNotice,
  liveCoverageIsCurrent,
  telemetryFreshnessPill,
} from '../app/threat-monitoring/live-coverage-copy';

const NOW = '2026-08-27T12:00:00Z';
// A historical security event, ~40 days old: the quiet-wallet case.
const OLD_SECURITY_EVENT = '2026-07-18T12:00:00Z';

function realtimeIngestion(overrides: Partial<RealtimeIngestionFactsPayload> = {}): RealtimeIngestionFactsPayload {
  return {
    streams_enabled: true,
    status: 'healthy',
    healthy: true,
    live_evidence_fresh: true,
    live_evidence_kind: 'coverage',
    live_coverage_fresh: true,
    live_security_telemetry_fresh: false,
    lane_state: 'live',
    lag_blocks: 1,
    checkpoint_age_seconds: 4,
    live_coverage_age_seconds: 20,
    live_telemetry_age_seconds: null,
    reason: 'stream_near_chain_tip_with_fresh_coverage',
    ...overrides,
  };
}

function workerStatus(overrides: Record<string, unknown> = {}): WorkerStatusSummary {
  return {
    stable_polling: {
      label: 'Stable RPC Polling',
      state: 'active',
      active: true,
      last_heartbeat_at: NOW,
      last_poll_at: NOW,
      heartbeat_age_seconds: 12,
      heartbeat_ttl_seconds: 900,
      detection_supported: true,
    },
    realtime: {
      label: 'Realtime WebSocket',
      enabled: false,
      state: 'paused',
      last_event_at: null,
      reason: 'BASE_REALTIME_ENABLED_not_true',
      fallback_active: false,
      provider_mode: null,
    },
    provider_realtime: {
      label: 'Provider realtime status',
      state: 'not_applicable',
      rate_limited: false,
      next_retry_at: null,
      host: null,
    },
    headline: 'Stable polling active. Realtime WebSocket paused.',
    monitoring_source_live: true,
    ...overrides,
  } as unknown as WorkerStatusSummary;
}

/**
 * The flat production /ops/monitoring/runtime-status response, carrying the exact
 * production evidence above: the runner decided `idle` (no monitored security event
 * in the window) while the Stream lane is healthy and the backend reports no reason.
 */
function runtimeStatusFixture(overrides: Record<string, unknown> = {}): MonitoringRuntimeStatus {
  return {
    workspace_configured: true,
    runtime_status: 'healthy',
    monitoring_status: 'idle',
    configured_systems: 1,
    reporting_systems: 1,
    protected_assets: 1,
    last_poll_at: NOW,
    last_heartbeat_at: NOW,
    last_telemetry_at: OLD_SECURITY_EVENT,
    last_detection_at: null,
    // Poll-derived SECURITY telemetry freshness — legitimately stale on a quiet wallet.
    freshness_status: 'stale',
    confidence_status: 'high',
    evidence_source: 'live',
    // The API substitutes 'unknown' when the backend reported status_reason=None.
    status_reason: 'unknown',
    contradiction_flags: [],
    provider_health_status: 'healthy',
    target_coverage_status: 'reporting',
    next_required_action: 'monitoring_live',
    worker_status: workerStatus(),
    realtime_enabled: false,
    realtime_ingestion: realtimeIngestion(),
    ...overrides,
  } as unknown as MonitoringRuntimeStatus;
}

function truthFor(overrides: Record<string, unknown> = {}) {
  return resolveWorkspaceMonitoringTruth(runtimeStatusFixture(overrides));
}

function bannerTextFor(overrides: Record<string, unknown> = {}): string {
  const truth = truthFor(overrides);
  const state = deriveBannerState(truth);
  if (state === 'LIVE') return '';
  return `${stateLabel(state)} ${compactReason(state, truth)} ${workerStatusBannerLine(truth) ?? ''}`;
}

// ── 1. idle + realtime healthy + live evidence fresh + no status_reason ──────

test.describe('1. idle under proven live coverage shows no limited banner', () => {
  test('the production evidence resolves to LIVE, not LIMITED_COVERAGE', () => {
    expect(deriveBannerState(truthFor())).toBe('LIVE');
  });

  test('no LIMITED COVERAGE label is produced for the idle runtime', () => {
    expect(bannerTextFor()).not.toContain('LIMITED COVERAGE');
  });

  test('the frontend manufactures no guard reason over the backend null', () => {
    const truth = truthFor();
    expect(truth.status_reason).toBeNull();
    expect(truth.guard_flags).toEqual([]);
    expect(truth.contradiction_flags).toEqual([]);
  });

  test("the idle_runtime_with_active_monitoring_claim guard does not fire on runtime_status='idle'", () => {
    const truth = truthFor({ runtime_status: 'idle' });
    expect(truth.guard_flags).not.toContain('idle_runtime_with_active_monitoring_claim');
    expect(deriveBannerState(truth)).toBe('LIVE');
  });

  test('the idle fact itself is preserved rather than rewritten to active', () => {
    expect(truthFor().monitoring_activity).toBe('idle');
  });

  test('idle + healthy carries optional neutral copy, never a warning', () => {
    expect(quietMonitoringNote(truthFor())).toBe('Monitoring live — no recent security events.');
  });

  test('fail-closed: idle without a canonical realtime verdict keeps the warning', () => {
    const truth = truthFor({ realtime_ingestion: undefined, runtime_status: 'idle' });
    expect(truth.guard_flags).toContain('idle_runtime_with_active_monitoring_claim');
    expect(deriveBannerState(truth)).toBe('LIMITED_COVERAGE');
    expect(quietMonitoringNote(truth)).toBeNull();
  });

  test('fail-closed: idle with a reported status reason keeps the warning', () => {
    const truth = truthFor({ status_reason: 'targets_blocked' });
    expect(deriveBannerState(truth)).not.toBe('LIVE');
    expect(quietMonitoringNote(truth)).toBeNull();
  });
});

// ── 2. active + healthy => no warning banner ─────────────────────────────────

test.describe('2. active + healthy shows no warning banner', () => {
  test('active resolves to LIVE', () => {
    expect(deriveBannerState(truthFor({ monitoring_status: 'active' }))).toBe('LIVE');
  });

  test('the banner component renders nothing for LIVE', () => {
    const source = fs.readFileSync(
      path.join(__dirname, '..', 'app', 'workspace-monitoring-mode-banner.tsx'),
      'utf-8',
    );
    expect(source).toContain("if (state === 'LIVE') return null;");
  });

  test('an active runtime carries no quiet-idle note', () => {
    expect(quietMonitoringNote(truthFor({ monitoring_status: 'active' }))).toBeNull();
  });
});

// ── 3. limited => warning shown ──────────────────────────────────────────────

test.describe('3. limited keeps a warning', () => {
  const limited = () => truthFor({
    monitoring_status: 'limited',
    runtime_status: 'degraded',
    status_reason: 'alerts_without_detection_evidence',
    contradiction_flags: ['alert_without_detection'],
  });

  test('limited resolves to LIMITED_COVERAGE with a warning label', () => {
    expect(deriveBannerState(limited())).toBe('LIMITED_COVERAGE');
    expect(stateLabel(deriveBannerState(limited()))).toBe('LIMITED COVERAGE');
  });

  test('the limited warning states the real condition', () => {
    expect(compactReason(deriveBannerState(limited()), limited())).toBeTruthy();
  });
});

// ── 4. degraded => warning shown ─────────────────────────────────────────────

test.describe('4. degraded keeps a warning', () => {
  const degraded = () => truthFor({
    monitoring_status: 'degraded',
    runtime_status: 'degraded',
    status_reason: 'targets_blocked',
    realtime_ingestion: realtimeIngestion({
      status: 'degraded',
      healthy: false,
      live_evidence_fresh: false,
      live_coverage_fresh: false,
      lane_state: 'degraded',
      reason: 'stream_far_behind_chain_tip',
    }),
  });

  test('degraded warns and names the Stream condition', () => {
    expect(deriveBannerState(degraded())).toBe('LIMITED_COVERAGE');
    expect(compactReason(deriveBannerState(degraded()), degraded()))
      .toBe('QuickNode Stream is behind the chain tip.');
  });
});

// ── 5. offline => warning shown ──────────────────────────────────────────────

test.describe('5. offline keeps a warning', () => {
  test('a confirmed offline runtime warns as OFFLINE', () => {
    const truth = truthFor({
      monitoring_status: 'offline',
      runtime_status: 'offline',
      protected_assets: 0,
      reporting_systems: 0,
      last_heartbeat_at: null,
      last_poll_at: null,
      last_telemetry_at: null,
      evidence_source: 'none',
      status_reason: 'live_worker_not_running',
      realtime_ingestion: realtimeIngestion({
        status: 'no_evidence',
        healthy: false,
        live_evidence_fresh: false,
        live_coverage_fresh: false,
        lane_state: null,
        reason: 'no_realtime_stream_evidence',
      }),
    });
    expect(deriveBannerState(truth)).toBe('OFFLINE');
    expect(compactReason('OFFLINE', truth)).toContain('Runtime offline');
  });
});

// ── 6. catching-up Stream => warning shown ───────────────────────────────────

test.describe('6. a catching-up QuickNode Stream keeps a warning', () => {
  const catchingUp = () => truthFor({
    realtime_ingestion: realtimeIngestion({
      status: 'degraded',
      healthy: false,
      live_evidence_fresh: false,
      live_coverage_fresh: false,
      lane_state: 'catching_up',
      lag_blocks: 480,
      reason: 'stream_live_lane_not_established',
    }),
  });

  test('catching up warns even though the runtime status still reads healthy', () => {
    expect(deriveBannerState(catchingUp())).toBe('LIMITED_COVERAGE');
    expect(compactReason(deriveBannerState(catchingUp()), catchingUp()))
      .toBe('QuickNode Stream is catching up to the chain tip.');
  });

  test('a catching-up Stream is never given the quiet-idle note', () => {
    expect(quietMonitoringNote(catchingUp())).toBeNull();
  });
});

// ── 7. stale historical security event + fresh live coverage ─────────────────

test.describe('7. an old security event is not monitoring staleness', () => {
  const facts = () => truthFor().realtime_ingestion;

  test('the canonical verdict reports live coverage as current', () => {
    expect(liveCoverageIsCurrent(facts())).toBe(true);
  });

  test('the top banner never claims live telemetry is stale under fresh coverage', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'alerts_without_detection_evidence',
      contradiction_flags: ['alert_without_detection'],
    });
    expect(compactReason(deriveBannerState(truth), truth)).not.toContain('Live telemetry is stale');
  });

  test("the page's telemetry_stale reason becomes neutral quiet-coverage copy", () => {
    const notice = coverageNotice(['telemetry_stale'], true);
    expect(notice).not.toBeNull();
    expect(notice?.text).toBe(QUIET_LIVE_COVERAGE_COPY);
    expect(notice?.tone).toBe('neutral');
    expect(notice?.text).not.toContain('stale');
  });

  test('the Telemetry Events pill reports no recent events, not a stale monitor', () => {
    const pill = telemetryFreshnessPill('stale', true, { label: 'Stale', variant: 'warning' });
    expect(pill.label).toBe('No recent events');
    expect(pill.variant).toBe('neutral');
  });

  test('"results may be incomplete because ingestion is stale" is suppressed under fresh coverage', () => {
    expect(coverageIncompleteWarningApplies(true, true)).toBe(false);
  });

  test('fail-closed: without fresh live coverage the stale wording stands', () => {
    const notice = coverageNotice(['telemetry_stale'], false);
    expect(notice?.tone).toBe('warning');
    expect(notice?.text).toBe('Telemetry is stale — detections may be based on older data.');
    expect(coverageIncompleteWarningApplies(true, false)).toBe(true);
    expect(telemetryFreshnessPill('stale', false, { label: 'Stale', variant: 'warning' }).label).toBe('Stale');
  });

  test('a genuine degraded reason is never hidden by the quiet-coverage copy', () => {
    const notice = coverageNotice(['telemetry_stale', 'worker_unhealthy'], true);
    expect(notice?.tone).toBe('warning');
    expect(notice?.text).toContain(QUIET_LIVE_COVERAGE_COPY);
    expect(notice?.text).toContain('The detection worker heartbeat is stale');
  });

  test('"no telemetry has ever arrived" keeps its own copy, it is a different fact', () => {
    const notice = coverageNotice(['no_telemetry'], true);
    expect(notice?.tone).toBe('warning');
    expect(notice?.text).toBe('No telemetry has arrived yet.');
  });
});

// ── 8. status_reason transitions from an old incident error to null ──────────

test.describe('8. a cleared status_reason disappears from the UI', () => {
  const withIncidentGuard = { status_reason: 'guard:incident_exists_without_alert' };

  test('while the response carries the reason it is shown', () => {
    const truth = truthFor(withIncidentGuard);
    expect(truth.status_reason).toBe('guard:incident_exists_without_alert');
    expect(deriveBannerState(truth)).not.toBe('LIVE');
  });

  test('once the response reports no reason the derivation carries nothing forward', () => {
    // Same fixture, next poll: the reason is gone. Nothing in the truth resolver
    // may re-introduce it — that stickiness is what pinned the obsolete limitation.
    const truth = truthFor();
    expect(truth.status_reason).toBeNull();
    expect(truth.guard_flags).toEqual([]);
    expect(deriveBannerState(truth)).toBe('LIVE');
    expect(bannerTextFor()).not.toContain('incident');
  });

  test('the raw guard wire code is never the customer-facing sentence', () => {
    const copy = runtimeReasonMessage('guard:incident_exists_without_alert');
    expect(copy).toBe('Incidents must be linked to at least one alert.');
    expect(copy).not.toContain('guard:');
    expect(copy).not.toContain('Runtime condition');
  });

  test('the runtime summary provider revalidates instead of fetching once per mount', () => {
    // Without periodic revalidation a status_reason the backend has already cleared
    // stayed pinned to the banner for the whole client session.
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-summary-context.tsx'), 'utf-8');
    expect(source).toContain('RUNTIME_STATUS_REVALIDATE_MS');
    expect(source).toContain('setInterval');
    expect(source).toContain('clearInterval');
  });

  test('the revalidation interval outlives the runtime-status client cache window', () => {
    const contextSource = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-summary-context.tsx'), 'utf-8');
    const clientSource = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-status-client.ts'), 'utf-8');
    const revalidate = Number(/RUNTIME_STATUS_REVALIDATE_MS = ([\d_]+)/.exec(contextSource)?.[1].replace(/_/g, ''));
    const freshness = Number(/RUNTIME_STATUS_FRESHNESS_MS = ([\d_]+)/.exec(clientSource)?.[1].replace(/_/g, ''));
    expect(Number.isFinite(revalidate)).toBe(true);
    expect(Number.isFinite(freshness)).toBe(true);
    expect(revalidate).toBeGreaterThan(freshness);
  });
});

// ── 9. fallback polling active while the primary Stream is healthy ───────────

test.describe('9. fallback polling never implies primary monitoring failure', () => {
  const fallbackActive = () => truthFor({
    worker_status: workerStatus({
      realtime: {
        label: 'Realtime WebSocket',
        enabled: true,
        state: 'degraded',
        last_event_at: null,
        reason: 'tls_internal_error',
        fallback_active: true,
        provider_mode: 'stable_rpc_polling_fallback',
      },
      headline: 'Stable polling active. Realtime degraded — stable polling fallback active.',
    }),
  });

  test('a healthy primary Stream still resolves to LIVE with fallback polling running', () => {
    expect(deriveBannerState(fallbackActive())).toBe('LIVE');
  });

  test('the worker line reports the fallback without claiming the Stream failed', () => {
    const line = workerStatusBannerLine(fallbackActive()) ?? '';
    expect(line).toContain('Stable polling active.');
    expect(line).not.toContain('QuickNode Stream is behind');
    expect(line).not.toContain('delivery is failing');
    expect(line).not.toContain('checkpoint is stale');
  });

  test('a paused legacy worker alongside a healthy Stream is not notable at all', () => {
    expect(workerStatusBannerLine(truthFor())).toBeNull();
  });
});

// ── 10. WebSocket is never presented as required realtime architecture ───────

test.describe('10. no WebSocket wording in customer-facing status copy', () => {
  test('no banner text mentions the legacy realtime WebSocket', () => {
    const cases = [
      {},
      { monitoring_status: 'limited', runtime_status: 'degraded', status_reason: 'targets_blocked' },
      { runtime_status: 'idle' },
      {
        realtime_ingestion: realtimeIngestion({
          status: 'degraded', healthy: false, live_evidence_fresh: false,
          live_coverage_fresh: false, lane_state: 'catching_up',
          reason: 'stream_live_lane_not_established',
        }),
      },
    ];
    for (const overrides of cases) {
      expect(bannerTextFor(overrides)).not.toContain('WebSocket');
    }
  });

  test('the quiet-coverage copy names no transport at all', () => {
    expect(QUIET_LIVE_COVERAGE_COPY).not.toContain('WebSocket');
    expect(quietMonitoringNote(truthFor()) ?? '').not.toContain('WebSocket');
  });

  test('the fallback-polling worker line drops the WebSocket clause', () => {
    const truth = truthFor({
      worker_status: workerStatus({
        realtime: {
          label: 'Realtime WebSocket',
          enabled: true,
          state: 'degraded',
          last_event_at: null,
          reason: 'tls_internal_error',
          fallback_active: true,
          provider_mode: 'stable_rpc_polling_fallback',
        },
        headline: 'Stable polling active. Realtime WebSocket degraded — stable polling fallback active.',
      }),
    });
    expect(workerStatusBannerLine(truth) ?? '').not.toContain('WebSocket');
  });
});

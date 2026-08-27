/**
 * Top monitoring status banner (rendered on every product page, Threat Monitoring
 * included) must be derived from the canonical /ops/monitoring/runtime-status
 * response.
 *
 * The regression these tests lock: with the backend reporting
 *   monitoring_status=active, status_reason=None, realtime_ingestion healthy,
 *   realtime live evidence fresh, chosen_evidence_source=live
 * the banner still showed "LIMITED COVERAGE / Live telemetry is stale. / Stable
 * polling active. Realtime WebSocket paused." — a stale frontend verdict built
 * from an unrecognised monitoring_status word and from the legacy realtime
 * WebSocket worker, which is intentionally disabled. QuickNode Streams is the
 * primary realtime monitoring path.
 *
 * Fail-closed remains the rule: limited / degraded / offline / catching-up Stream
 * all keep the warning banner with the real current reason.
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
import { resolveWorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';

const NOW = '2026-08-27T12:00:00Z';

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

// The legacy realtime WebSocket worker is intentionally disabled in production, so
// the canonical worker_status always carries the paused headline.
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

/** The flat production /ops/monitoring/runtime-status response shape. */
function runtimeStatusFixture(overrides: Record<string, unknown> = {}): MonitoringRuntimeStatus {
  return {
    workspace_configured: true,
    runtime_status: 'healthy',
    monitoring_status: 'active',
    configured_systems: 2,
    reporting_systems: 2,
    protected_assets: 1,
    last_poll_at: NOW,
    last_heartbeat_at: NOW,
    last_telemetry_at: NOW,
    last_detection_at: null,
    // The summary-level freshness field tracks SECURITY telemetry (a matched
    // wallet transfer), which is legitimately stale on a quiet wallet. It must not
    // be reported as "live telemetry is stale" while realtime coverage is current.
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

// ── Canonical vocabulary ─────────────────────────────────────────────────────

test.describe('canonical runtime-status vocabulary', () => {
  test("monitoring_status='active' resolves to live, not limited", () => {
    expect(truthFor().monitoring_status).toBe('live');
  });

  test("runtime_status='healthy' resolves to live", () => {
    expect(truthFor().runtime_status).toBe('live');
  });

  test("monitoring_status='idle' stays limited — idle is not live", () => {
    expect(truthFor({ monitoring_status: 'idle' }).monitoring_status).toBe('limited');
  });

  test("monitoring_status='degraded' stays limited", () => {
    expect(truthFor({ monitoring_status: 'degraded' }).monitoring_status).toBe('limited');
  });

  test("the API's 'unknown' status_reason placeholder is not treated as a reason", () => {
    expect(truthFor().status_reason).toBeNull();
  });

  test("evidence_source='live_provider' is read as the live evidence source", () => {
    expect(truthFor({ evidence_source: 'live_provider' }).evidence_source_summary).toBe('live');
  });

  test('canonical realtime_ingestion facts are read from the top-level response', () => {
    const truth = truthFor();
    expect(truth.realtime_ingestion?.healthy).toBe(true);
    expect(truth.realtime_ingestion?.live_evidence_fresh).toBe(true);
    expect(truth.realtime_ingestion?.live_evidence_kind).toBe('coverage');
  });

  test('a missing realtime_ingestion block is null, never assumed healthy', () => {
    const truth = truthFor({ realtime_ingestion: undefined });
    expect(truth.realtime_ingestion).toBeNull();
  });

  test('fresh realtime rows behind an unhealthy lane are not read as fresh live evidence', () => {
    const truth = truthFor({
      realtime_ingestion: realtimeIngestion({
        status: 'stale',
        healthy: false,
        live_evidence_fresh: true,
        live_coverage_fresh: true,
      }),
    });
    expect(truth.realtime_ingestion?.live_evidence_fresh).toBe(false);
    expect(truth.realtime_ingestion?.live_coverage_fresh).toBe(false);
  });
});

// ── active → no warning banner ───────────────────────────────────────────────

test.describe('active runtime shows no warning banner', () => {
  test('active + realtime healthy + live evidence fresh resolves to LIVE', () => {
    expect(deriveBannerState(truthFor())).toBe('LIVE');
  });

  test('LIVE renders no warning strip (the component returns null for LIVE)', () => {
    const bannerSource = fs.readFileSync(
      path.join(__dirname, '..', 'app', 'workspace-monitoring-mode-banner.tsx'),
      'utf-8',
    );
    expect(bannerSource).toContain("if (state === 'LIVE') return null;");
  });

  test('an active runtime never reports LIMITED COVERAGE', () => {
    expect(stateLabel(deriveBannerState(truthFor()))).not.toBe('LIMITED COVERAGE');
  });

  test('coverage-only live evidence (quiet wallet) still counts as fresh live evidence', () => {
    const truth = truthFor({
      last_telemetry_at: null,
      realtime_ingestion: realtimeIngestion({
        live_security_telemetry_fresh: false,
        live_coverage_fresh: true,
        live_evidence_kind: 'coverage',
      }),
    });
    expect(deriveBannerState(truth)).toBe('LIVE');
  });

  test('a limited monitoring status caused only by the poll-based freshness field still resolves LIVE', () => {
    // _normalized_monitoring_status downgrades to 'limited' whenever the 900s
    // reconciliation poll's coverage timestamp is outside the window, even while the
    // Stream lane is delivering. With no reported reason and no contradiction, the
    // canonical realtime verdict is the authority on whether coverage is current.
    const truth = truthFor({ monitoring_status: 'limited', runtime_status: 'degraded' });
    expect(deriveBannerState(truth)).toBe('LIVE');
  });

  test('proven realtime coverage suppresses the poll-without-telemetry timestamp guard', () => {
    const truth = truthFor({ last_telemetry_at: null });
    expect(truth.guard_flags).not.toContain('poll_without_telemetry_timestamp');
  });

  test('without a realtime verdict the timestamp guard still fires (fail-closed)', () => {
    const truth = truthFor({ last_telemetry_at: null, realtime_ingestion: undefined });
    expect(truth.guard_flags).toContain('poll_without_telemetry_timestamp');
  });
});

// ── limited / degraded / offline keep the warning ────────────────────────────

test.describe('degraded conditions keep the warning banner', () => {
  test('limited monitoring status warns', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'alerts_without_detection_evidence',
      contradiction_flags: ['alert_without_detection'],
    });
    expect(deriveBannerState(truth)).toBe('LIMITED_COVERAGE');
    expect(stateLabel(deriveBannerState(truth))).toBe('LIMITED COVERAGE');
  });

  test('limited monitoring status with no canonical realtime verdict warns', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'no_fresh_live_coverage_telemetry',
      realtime_ingestion: undefined,
    });
    expect(deriveBannerState(truth)).toBe('LIMITED_COVERAGE');
  });

  test('degraded monitoring status warns', () => {
    const truth = truthFor({
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
    expect(deriveBannerState(truth)).toBe('LIMITED_COVERAGE');
    expect(compactReason(deriveBannerState(truth), truth)).toBe('QuickNode Stream is behind the chain tip.');
  });

  test('offline runtime warns', () => {
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

  test('a database failure stays OFFLINE and names the database', () => {
    const truth = truthFor({
      status_reason: 'runtime_status_degraded:database_error',
      db_failure_reason: 'connection refused',
    });
    expect(deriveBannerState(truth)).toBe('OFFLINE');
    expect(compactReason('OFFLINE', truth)).toContain('backend database is unavailable');
  });

  test('a real status reason blocks the canonical live verdict', () => {
    const truth = truthFor({ status_reason: 'coverage_only_persistent_no_evidence' });
    expect(deriveBannerState(truth)).not.toBe('LIVE');
  });

  test('an outstanding contradiction blocks the canonical live verdict', () => {
    const truth = truthFor({ contradiction_flags: ['alert_without_detection'] });
    expect(deriveBannerState(truth)).not.toBe('LIVE');
  });
});

// ── catching-up Stream ───────────────────────────────────────────────────────

test.describe('catching-up QuickNode Stream', () => {
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

  test('a catching-up Stream keeps the warning banner even while runtime reads live', () => {
    expect(deriveBannerState(catchingUp())).toBe('LIMITED_COVERAGE');
  });

  test('the catching-up warning names the Stream condition', () => {
    const truth = catchingUp();
    expect(compactReason(deriveBannerState(truth), truth)).toBe(
      'QuickNode Stream is catching up to the chain tip.',
    );
  });

  test('a stalled Stream checkpoint keeps the warning banner', () => {
    const truth = truthFor({
      realtime_ingestion: realtimeIngestion({
        status: 'stale',
        healthy: false,
        live_evidence_fresh: false,
        live_coverage_fresh: false,
        lane_state: 'stale',
        reason: 'stream_checkpoint_stale',
      }),
    });
    expect(deriveBannerState(truth)).toBe('LIMITED_COVERAGE');
    expect(compactReason(deriveBannerState(truth), truth)).toBe('QuickNode Stream checkpoint is stale.');
  });
});

// ── never claim stale telemetry against fresh live coverage ──────────────────

test.describe('telemetry staleness copy', () => {
  test('does not claim live telemetry is stale when runtime-status reports fresh live coverage', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'alerts_without_detection_evidence',
      contradiction_flags: ['alert_without_detection'],
    });
    const reason = compactReason(deriveBannerState(truth), truth);
    expect(reason).not.toContain('Live telemetry is stale');
    expect(reason).toBe('Live coverage is current; monitoring coverage is still incomplete.');
  });

  test('still says telemetry is stale when there is no fresh live coverage to contradict it', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'stale_telemetry',
      realtime_ingestion: undefined,
    });
    expect(compactReason(deriveBannerState(truth), truth)).toBe('Live telemetry is stale.');
  });
});

// ── obsolete WebSocket wording is gone ───────────────────────────────────────

test.describe('realtime WebSocket wording', () => {
  test('the worker line drops the paused-WebSocket clause entirely', () => {
    // Stable polling active + healthy Stream = nothing notable to report.
    expect(workerStatusBannerLine(truthFor())).toBeNull();
  });

  test('no banner text mentions the legacy realtime WebSocket', () => {
    const states = [
      truthFor(),
      truthFor({ monitoring_status: 'limited', runtime_status: 'degraded', status_reason: 'targets_blocked' }),
      truthFor({
        realtime_ingestion: realtimeIngestion({
          status: 'degraded', healthy: false, live_evidence_fresh: false,
          live_coverage_fresh: false, lane_state: 'catching_up',
          reason: 'stream_live_lane_not_established',
        }),
      }),
    ];
    for (const truth of states) {
      const text = `${compactReason(deriveBannerState(truth), truth)} ${workerStatusBannerLine(truth) ?? ''}`;
      expect(text).not.toContain('WebSocket');
    }
  });

  test('a stale stable-polling worker is still reported, without WebSocket wording', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'stale_heartbeat',
      worker_status: workerStatus({
        stable_polling: {
          label: 'Stable RPC Polling',
          state: 'stale',
          active: false,
          last_heartbeat_at: '2026-08-27T09:00:00Z',
          last_poll_at: '2026-08-27T09:00:00Z',
          heartbeat_age_seconds: 10800,
          heartbeat_ttl_seconds: 900,
          detection_supported: false,
        },
        headline: 'RPC polling worker heartbeat is stale.',
      }),
    });
    const line = workerStatusBannerLine(truth);
    expect(line).toContain('RPC polling worker heartbeat is stale.');
    expect(line).not.toContain('WebSocket');
  });

  test('fallback polling warning never claims a WebSocket failure', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'realtime_paused_stable_polling_active',
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
      realtime_ingestion: realtimeIngestion({
        status: 'degraded',
        healthy: false,
        live_evidence_fresh: false,
        live_coverage_fresh: false,
        lane_state: 'degraded',
        reason: 'stream_far_behind_chain_tip',
      }),
    });
    const line = workerStatusBannerLine(truth) ?? '';
    expect(line).not.toContain('WebSocket');
    expect(line).toContain('Stable polling active.');
    expect(line).toContain('stable polling fallback active');
    expect(line).toContain('QuickNode Stream is behind the chain tip.');
  });
});

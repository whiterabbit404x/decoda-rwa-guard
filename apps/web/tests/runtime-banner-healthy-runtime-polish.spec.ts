/**
 * Runtime banner — healthy/live state presentation.
 *
 * Production evidence this locks (a fully healthy workspace):
 *   monitoring_status=active, decision=active, status_reason=live_runtime_verified,
 *   chosen_evidence_source=live, realtime_ingestion_healthy=True,
 *   realtime_live_evidence_fresh=True
 *
 * The banner correctly reported MONITORING: Live / FRESHNESS: Live coverage current /
 * CONFIDENCE: Verified, and then contradicted itself with three fields that only make
 * sense on a degraded runtime:
 *
 *   NEXT ACTION: Review runtime reason codes   (no operator action exists)
 *   WORKERS:     Stable polling active.        (reads as if polling were primary)
 *   LIMITATION:  Runtime condition: live runtime verified.  (a SUCCESS reason)
 *
 * Fixed here, all three derived from canonical runtime facts:
 *   1. `live_runtime_verified` is a success reason, so it never reaches the
 *      limitation surface (runtime-reason-copy.isSuccessRuntimeReason).
 *   2. Next action renders only when an operator actually has something to do
 *      (workspace-monitoring-truth.runtimeRequiresOperatorAction).
 *   3. While the PRIMARY realtime path (QuickNode Streams) is proven healthy, the RPC
 *      polling loop is the fallback standing by — reported as ready, not as the
 *      mechanism carrying monitoring.
 *
 * Fail-closed stays the rule: every limited / degraded / offline state keeps its
 * limitation and its next action, a fallback that has actually taken over keeps the
 * stronger wording, and the quiet-wallet copy is untouched.
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
  runtimeBannerLimitation,
  runtimeBannerNextAction,
  runtimeBannerTopReason,
} from '../app/components/runtime-banner';
import {
  FALLBACK_POLLING_READY_COPY,
  realtimeWorkerStatusLine,
} from '../app/realtime-coverage-status';
import { isSuccessRuntimeReason, runtimeReasonMessage } from '../app/runtime-reason-copy';
import {
  quietMonitoringNote,
  resolveWorkspaceMonitoringTruth,
  runtimeIsHealthyLive,
  runtimeRequiresOperatorAction,
  type WorkspaceMonitoringTruth,
} from '../app/workspace-monitoring-truth';
import {
  QUIET_LIVE_COVERAGE_COPY,
  coverageNotice,
} from '../app/threat-monitoring/live-coverage-copy';

const NOW = '2026-08-28T12:00:00Z';
// The provider's fallback label the runtime summary hands the banner when the
// backend action is not one this banner names itself.
const CONTEXT_FALLBACK_LABEL = 'Review runtime reason codes';

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
    lag_blocks: 0,
    checkpoint_age_seconds: 3,
    live_coverage_age_seconds: 15,
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
      heartbeat_age_seconds: 9,
      heartbeat_ttl_seconds: 900,
      detection_supported: true,
    },
    realtime: {
      label: 'Realtime WebSocket',
      enabled: false,
      state: 'paused',
      last_event_at: null,
      reason: 'BASE_REALTIME_ENABLED_not_true',
      // The RPC polling loop is intentionally kept available behind the Stream.
      fallback_active: true,
      provider_mode: 'stable_rpc_polling_fallback',
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

/** The flat production /ops/monitoring/runtime-status response for the healthy state. */
function runtimeStatusFixture(overrides: Record<string, unknown> = {}): MonitoringRuntimeStatus {
  return {
    workspace_configured: true,
    runtime_status: 'healthy',
    monitoring_status: 'active',
    configured_systems: 1,
    reporting_systems: 1,
    protected_assets: 1,
    last_poll_at: NOW,
    last_heartbeat_at: NOW,
    last_telemetry_at: NOW,
    last_coverage_telemetry_at: NOW,
    last_detection_at: null,
    freshness_status: 'fresh',
    confidence_status: 'high',
    evidence_source: 'live',
    status_reason: 'live_runtime_verified',
    contradiction_flags: [],
    continuity_status: 'continuous_live',
    continuity_reason_codes: [],
    provider_health_status: 'healthy',
    target_coverage_status: 'reporting',
    next_required_action: 'monitoring_live',
    worker_status: workerStatus(),
    realtime_enabled: false,
    realtime_ingestion: realtimeIngestion(),
    ...overrides,
  } as unknown as MonitoringRuntimeStatus;
}

function truthFor(overrides: Record<string, unknown> = {}): WorkspaceMonitoringTruth {
  return resolveWorkspaceMonitoringTruth(runtimeStatusFixture(overrides));
}

/** The Workers field value, built exactly as the banner builds it. */
function workerFieldFor(truth: WorkspaceMonitoringTruth): string | null {
  return realtimeWorkerStatusLine(truth.worker_status ?? null, truth.realtime_ingestion);
}

// ── 1. a success reason is not a limitation ─────────────────────────────────

test.describe('1. live_runtime_verified is a success reason, never a limitation', () => {
  test('the canonical healthy payload resolves to a live, verified runtime', () => {
    const truth = truthFor();
    expect(truth.monitoring_status).toBe('live');
    expect(truth.runtime_status).toBe('live');
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect(truth.guard_flags).toEqual([]);
    expect(truth.contradiction_flags).toEqual([]);
    expect(runtimeIsHealthyLive(truth)).toBe(true);
  });

  test('live_runtime_verified is classified as a success reason', () => {
    expect(isSuccessRuntimeReason('live_runtime_verified')).toBe(true);
    // Every degraded code stays a limitation.
    for (const code of [
      'stale_telemetry',
      'stale_heartbeat',
      'targets_blocked',
      'no_reporting_systems',
      'no_fresh_live_coverage_telemetry',
      'guard:live_telemetry_verified_without_timestamp',
      'summary_unavailable',
    ]) {
      expect(isSuccessRuntimeReason(code)).toBe(false);
    }
  });

  test('the healthy runtime renders NO limitation field', () => {
    expect(runtimeBannerLimitation(truthFor())).toBeNull();
  });

  test('the obsolete "Runtime condition: live runtime verified." sentence is gone', () => {
    // The old copy came from the generic fallback template. The code now has positive
    // copy, and the limitation surface drops it either way.
    expect(runtimeBannerLimitation(truthFor()) ?? '').not.toContain('live runtime verified');
    expect(runtimeReasonMessage('live_runtime_verified')).toBe('Live runtime verified.');
    expect(runtimeReasonMessage('live_runtime_verified')).not.toContain('Runtime condition:');
  });
});

// ── 2. no next action on a healthy runtime ──────────────────────────────────

test.describe('2. a healthy runtime asks the operator for nothing', () => {
  test('no "Review runtime reason codes" instruction is rendered', () => {
    const value = runtimeBannerNextAction(truthFor(), CONTEXT_FALLBACK_LABEL);
    expect(value).toBeNull();
    expect(value ?? '').not.toContain('Review runtime reason codes');
  });

  test('the backend "monitoring_live" action means no operator action', () => {
    expect(runtimeRequiresOperatorAction(truthFor())).toBe(false);
  });

  test('a healthy runtime that only carries the review-reason-codes placeholder is silent too', () => {
    // Some flat payload shapes omit next_required_action; the resolver then defaults to
    // 'review_reason_codes'. There are no reason codes worth reviewing when the only
    // reported reason is a success one.
    const truth = truthFor({ next_required_action: undefined });
    expect(truth.next_required_action).toBe('review_reason_codes');
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).toBeNull();
  });

  test('a real workflow step still renders on a healthy runtime', () => {
    // Nothing is wrong, but evidence has not been exported yet: that is workflow
    // progress, not a limitation, and the operator still needs to see it.
    const truth = truthFor({ next_required_action: 'export_evidence_package' });
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).toBe('Export evidence');
  });
});

// ── 3. fallback polling is not the primary realtime path ────────────────────

test.describe('3. fallback polling copy never implies polling is primary', () => {
  test('a healthy Stream reports the polling loop as a ready fallback', () => {
    const line = workerFieldFor(truthFor());
    expect(line).toBe(FALLBACK_POLLING_READY_COPY);
    expect(line ?? '').not.toContain('Stable polling active');
    expect(line ?? '').not.toContain('WebSocket');
  });

  test('the fallback copy names no primary monitoring role for polling', () => {
    expect(FALLBACK_POLLING_READY_COPY).toBe('Fallback polling ready.');
    expect(FALLBACK_POLLING_READY_COPY.toLowerCase()).toContain('fallback');
    expect(FALLBACK_POLLING_READY_COPY.toLowerCase()).not.toContain('active');
  });

  test('the copy is derived from the canonical verdict, not a fixed state', () => {
    // Same worker payload, Stream no longer healthy: the stronger wording returns
    // because polling has actually become the monitoring path.
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'realtime_paused_stable_polling_active',
      realtime_ingestion: realtimeIngestion({
        status: 'degraded',
        healthy: false,
        live_evidence_fresh: false,
        live_coverage_fresh: false,
        lane_state: 'degraded',
        reason: 'stream_far_behind_chain_tip',
      }),
      worker_status: workerStatus({
        headline: 'Stable polling active. Realtime degraded — stable polling fallback active.',
      }),
    });
    const line = workerFieldFor(truth) ?? '';
    expect(line).toContain('Stable polling active.');
    expect(line).toContain('QuickNode Stream is behind the chain tip.');
    expect(line).not.toBe(FALLBACK_POLLING_READY_COPY);
  });

  test('a stopped polling loop is still reported, healthy Stream or not', () => {
    const truth = truthFor({
      worker_status: workerStatus({
        stable_polling: {
          label: 'Stable RPC Polling',
          state: 'stale',
          active: false,
          last_heartbeat_at: '2026-08-28T09:00:00Z',
          last_poll_at: '2026-08-28T09:00:00Z',
          heartbeat_age_seconds: 10800,
          heartbeat_ttl_seconds: 900,
          detection_supported: false,
        },
        headline: 'RPC polling worker heartbeat is stale.',
      }),
    });
    expect(workerFieldFor(truth)).toContain('RPC polling worker heartbeat is stale.');
  });
});

// ── 4. real limitations stay visible (fail-closed) ──────────────────────────

test.describe('4. limited / degraded / offline keep their limitation', () => {
  const cases: Array<{ name: string; overrides: Record<string, unknown>; expected: string }> = [
    {
      name: 'limited coverage — nothing is reporting',
      overrides: {
        monitoring_status: 'limited',
        runtime_status: 'degraded',
        reporting_systems: 0,
        status_reason: 'no_reporting_systems',
        realtime_ingestion: realtimeIngestion({
          status: 'no_evidence', healthy: false, live_evidence_fresh: false,
          live_coverage_fresh: false, lane_state: null, reason: 'no_realtime_stream_evidence',
        }),
      },
      // Zero reporting systems fires the frontend's own coverage guard, which wins the
      // status reason — CLAUDE.md: monitoring is never healthy while nothing reports.
      expected: 'Live monitoring requires at least one reporting monitored system.',
    },
    {
      name: 'degraded — telemetry is stale',
      overrides: {
        monitoring_status: 'limited',
        runtime_status: 'degraded',
        freshness_status: 'stale',
        status_reason: 'stale_telemetry',
        realtime_ingestion: undefined,
      },
      expected: 'Telemetry is stale. Investigate worker health and source ingestion lag.',
    },
    {
      name: 'offline — the monitoring worker is not running',
      overrides: {
        monitoring_status: 'offline',
        runtime_status: 'offline',
        last_heartbeat_at: null,
        last_poll_at: null,
        last_telemetry_at: null,
        last_coverage_telemetry_at: null,
        freshness_status: 'unavailable',
        confidence_status: 'unavailable',
        status_reason: 'live_worker_not_running',
        worker_status: undefined,
        realtime_ingestion: undefined,
      },
      expected: 'The monitoring worker is not running. Deploy the worker service with WORKER_ENABLED=true and EVM_RPC_URL set.',
    },
    {
      name: 'blocked targets on an otherwise live runtime',
      overrides: { status_reason: 'targets_blocked' },
      expected: 'The monitoring worker is alive, but one or more targets are blocked (dead-lettered) and are not being polled. Recover the affected target(s) to resume live coverage.',
    },
  ];

  for (const { name, overrides, expected } of cases) {
    test(`${name} still renders its limitation`, () => {
      const truth = truthFor(overrides);
      expect(runtimeIsHealthyLive(truth)).toBe(false);
      expect(runtimeBannerLimitation(truth)).toBe(expected);
    });
  }

  test('a frontend guard firing under live_runtime_verified is still a limitation', () => {
    // Fail-closed: the backend claims a verified runtime while reporting telemetry as
    // unavailable with high confidence. The derived guard wins the status reason, so a
    // success code can never launder a contradiction off the banner.
    const truth = truthFor({ freshness_status: 'unavailable', confidence_status: 'high' });
    expect(truth.status_reason).toBe('guard:telemetry_unavailable_with_high_confidence');
    expect(runtimeIsHealthyLive(truth)).toBe(false);
    expect(runtimeBannerLimitation(truth)).not.toBeNull();
  });

  test('the two pre-existing worker-aware suppressions are unchanged', () => {
    // A stale-heartbeat / EVM_RPC_URL reason is still dropped only while the stable
    // polling worker is proven active, and still shown when it is not.
    for (const reason of ['stale_heartbeat', 'no_fresh_live_coverage_telemetry']) {
      const active = truthFor({ monitoring_status: 'limited', runtime_status: 'degraded', status_reason: reason });
      expect(runtimeBannerLimitation(active)).toBeNull();
      const stopped = truthFor({
        monitoring_status: 'limited',
        runtime_status: 'degraded',
        status_reason: reason,
        worker_status: workerStatus({
          stable_polling: {
            label: 'Stable RPC Polling', state: 'offline', active: false,
            last_heartbeat_at: null, last_poll_at: null,
            heartbeat_age_seconds: null, heartbeat_ttl_seconds: 900, detection_supported: false,
          },
          headline: 'RPC polling worker is not reporting.',
        }),
      });
      expect(runtimeBannerLimitation(stopped)).toBe(runtimeReasonMessage(reason));
    }
  });
});

// ── 5. real next actions stay visible (fail-closed) ─────────────────────────

test.describe('5. a runtime reason requiring action keeps its next action', () => {
  test('a genuine reason renders its operator instruction', () => {
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'no_fresh_live_coverage_telemetry',
      next_required_action: 'diagnose_ingestion',
      realtime_ingestion: realtimeIngestion({
        status: 'stale', healthy: false, live_evidence_fresh: false,
        live_coverage_fresh: false, lane_state: 'stale', reason: 'stream_checkpoint_stale',
      }),
    });
    expect(runtimeRequiresOperatorAction(truth)).toBe(true);
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).toBe('Diagnose ingestion');
  });

  test('setup states keep their setup instruction', () => {
    const truth = truthFor({
      workspace_configured: false,
      monitoring_status: 'offline',
      runtime_status: 'offline',
      protected_assets: 0,
      reporting_systems: 0,
      configured_systems: 0,
      status_reason: 'workspace_unconfigured',
      next_required_action: 'add_asset',
      worker_status: undefined,
      realtime_ingestion: undefined,
    });
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).toBe('Add protected asset');
  });

  test('a degraded runtime is never silenced by a stale "monitoring_live" action', () => {
    // Fail-closed: the health verdict decides whether the field may be hidden, not the
    // action word on its own.
    const truth = truthFor({
      monitoring_status: 'limited',
      runtime_status: 'degraded',
      status_reason: 'stale_telemetry',
      freshness_status: 'stale',
      next_required_action: 'monitoring_live',
      realtime_ingestion: undefined,
    });
    expect(runtimeRequiresOperatorAction(truth)).toBe(true);
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).toBe(CONTEXT_FALLBACK_LABEL);
  });

  test('an unavailable runtime summary still asks for action', () => {
    const truth = resolveWorkspaceMonitoringTruth(null);
    expect(runtimeRequiresOperatorAction(truth)).toBe(true);
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).not.toBeNull();
  });
});

// ── 6. quiet-wallet copy is preserved ───────────────────────────────────────

test.describe('6. quiet wallet with fresh live coverage keeps its neutral copy', () => {
  const quiet = () => truthFor({
    monitoring_status: 'idle',
    status_reason: 'unknown',
    last_telemetry_at: '2026-07-18T12:00:00Z',
    freshness_status: 'stale',
    next_required_action: 'monitoring_live',
  });

  test('the quiet-coverage sentence is unchanged', () => {
    expect(QUIET_LIVE_COVERAGE_COPY).toBe('No recent security events. Live monitoring coverage is current.');
    expect(coverageNotice(['telemetry_stale'], true)).toEqual({
      text: QUIET_LIVE_COVERAGE_COPY,
      tone: 'neutral',
    });
  });

  test('the banner keeps its neutral activity note and adds no limitation or to-do', () => {
    const truth = quiet();
    expect(truth.monitoring_status).toBe('live');
    expect(truth.monitoring_activity).toBe('idle');
    expect(quietMonitoringNote(truth)).toBe('Monitoring live — no recent security events.');
    expect(runtimeBannerLimitation(truth)).toBeNull();
    expect(runtimeBannerNextAction(truth, CONTEXT_FALLBACK_LABEL)).toBeNull();
  });
});

// ── 7. the component renders exactly these derivations ──────────────────────

test.describe('7. the banner component is wired to the canonical derivations', () => {
  const bannerSource = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'components', 'runtime-banner.tsx'), 'utf8',
  );

  test('the rendered fields come from the tested helpers', () => {
    expect(bannerSource).toContain('runtimeBannerNextAction(summary, contextNextActionLabel)');
    expect(bannerSource).toContain('runtimeBannerLimitation(summary, reasonMessageForCode)');
    expect(bannerSource).toContain('runtimeBannerTopReason(summary)');
  });

  test('Next action and Limitation are conditional fields', () => {
    expect(bannerSource).toContain('{nextActionDisplay ? (');
    expect(bannerSource).toContain('{reasonCopy ? (');
    // The always-on facts stay always-on.
    expect(bannerSource).toContain('<Field label="Monitoring" value={monitoringValue} />');
    expect(bannerSource).toContain('<Field label="Freshness" value={freshnessValue} />');
    expect(bannerSource).toContain('<Field label="Confidence" value={confidenceValue} />');
  });

  test('the healthy state renders Live / Live coverage current / Verified', () => {
    // Task 4: the correct copy must survive this change.
    const truth = truthFor();
    expect(truth.realtime_ingestion?.healthy).toBe(true);
    expect(truth.realtime_ingestion?.live_coverage_fresh).toBe(true);
    expect(truth.confidence).toBe('high');
    expect(truth.last_telemetry_at).toBe(NOW);
    expect(runtimeBannerTopReason(truth)).toBe('live_runtime_verified');
  });
});

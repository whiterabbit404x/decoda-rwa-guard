import { expect, test } from '@playwright/test';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';

/**
 * Loaded dynamically rather than with a static import: Playwright transpiles
 * specs to CommonJS, which cannot statically require a native ES module. A
 * runtime import() survives that transform and Node resolves the .mjs itself.
 */
const HARNESS_PATH = path.join(__dirname, '../../../scripts/capture-dashboard-timings.mjs');

type Harness = {
  backendDeltas: (before: Map<string, number>, after: Map<string, number>) => {
    phases: Record<string, number>;
    counters: Record<string, number>;
    flags: Record<string, number>;
    requests: Record<string, number>;
  };
  parseMetrics: (text: string) => Map<string, number>;
  redact: (text: string) => string;
  sumCounter: (counters: Record<string, number>, name: string) => number | null;
  sumPhase: (phases: Record<string, number>, name: string) => number | null;
};

let backendDeltas: Harness['backendDeltas'];
let parseMetrics: Harness['parseMetrics'];
let redact: Harness['redact'];
let sumCounter: Harness['sumCounter'];
let sumPhase: Harness['sumPhase'];

test.beforeAll(async () => {
  const harness: Harness = await import(pathToFileURL(HARNESS_PATH).href);
  ({ backendDeltas, parseMetrics, redact, sumCounter, sumPhase } = harness);
});

/**
 * The capture harness turns /metrics scrapes into the timing table the dashboard
 * latency work is argued from. Two things have to hold:
 *
 *   - the arithmetic is right, because a wrong delta is worse than no number at
 *     all — it looks like evidence, and
 *   - nothing secret reaches the artifact, because the artifact is meant to be
 *     pasted into a conversation.
 */

test.describe('metric parsing and per-load deltas', () => {
  test('parses labelled and unlabelled series', () => {
    const series = parseMetrics(
      [
        '# a comment line',
        'decoda_dashboard_request_seconds_sum{route="ops_dashboard_executive_summary"} 6.5',
        'decoda_stream_connections_active 3',
        '',
      ].join('\n'),
    );

    expect(series.get('decoda_dashboard_request_seconds_sum{route="ops_dashboard_executive_summary"}')).toBe(6.5);
    expect(series.get('decoda_stream_connections_active')).toBe(3);
    expect(series.has('# a comment line')).toBe(false);
  });

  test('a load reports the delta, not the running total', () => {
    // /metrics is cumulative, so a second load against a warm process must not
    // report the first load's time as its own.
    const before = parseMetrics(
      [
        'decoda_dashboard_phase_seconds_sum{phase="db_connect_ms",route="ops_dashboard_executive_summary"} 1.0',
        'decoda_dashboard_counter_total{counter="db_connect_count",route="ops_dashboard_executive_summary"} 5',
      ].join('\n'),
    );
    const after = parseMetrics(
      [
        'decoda_dashboard_phase_seconds_sum{phase="db_connect_ms",route="ops_dashboard_executive_summary"} 1.6',
        'decoda_dashboard_counter_total{counter="db_connect_count",route="ops_dashboard_executive_summary"} 10',
      ].join('\n'),
    );

    const deltas = backendDeltas(before, after);
    // 0.6s of new connect time, reported in ms.
    expect(sumPhase(deltas.phases, 'db_connect_ms')).toBeCloseTo(600, 1);
    expect(sumCounter(deltas.counters, 'db_connect_count')).toBe(5);
  });

  test('a phase recorded under two routes is summed across them', () => {
    const before = parseMetrics('');
    const after = parseMetrics(
      [
        'decoda_dashboard_phase_seconds_sum{phase="db_connect_ms",route="ops_dashboard_executive_summary"} 0.3',
        'decoda_dashboard_phase_seconds_sum{phase="db_connect_ms",route="auth_me"} 0.2',
      ].join('\n'),
    );

    // One dashboard load spans several instrumented requests; the connection
    // cost is the total across them, not whichever route is read first.
    expect(sumPhase(backendDeltas(before, after).phases, 'db_connect_ms')).toBeCloseTo(500, 1);
  });

  test('cache and single-flight state is reported per load', () => {
    const before = parseMetrics(
      'decoda_dashboard_flag_total{flag="runtime_status_cache",route="ops_dashboard_executive_summary",state="miss"} 2',
    );
    const after = parseMetrics(
      [
        'decoda_dashboard_flag_total{flag="runtime_status_cache",route="ops_dashboard_executive_summary",state="miss"} 2',
        'decoda_dashboard_flag_total{flag="runtime_status_cache",route="ops_dashboard_executive_summary",state="hit"} 1',
      ].join('\n'),
    );

    const { flags } = backendDeltas(before, after);
    expect(flags['ops_dashboard_executive_summary:runtime_status_cache=hit']).toBe(1);
    // The unchanged miss counter must not show up as this load's state.
    expect(flags).not.toHaveProperty(['ops_dashboard_executive_summary:runtime_status_cache=miss']);
  });

  test('an absent phase is null rather than zero', () => {
    // "Not measured" and "measured as zero" are different facts.
    expect(sumPhase({}, 'rpc_probe_ms')).toBeNull();
    expect(sumCounter({}, 'db_connect_count')).toBeNull();
  });
});

test.describe('redaction keeps secrets out of the artifact', () => {
  const FAKE_JWT = 'notarealheader.notarealpayload.notarealsignature';
  const FAKE_SESSION_ID = 'deadbeef'.repeat(8);

  test('strips a JWT', () => {
    const output = redact(`Error: request failed with Bearer ${FAKE_JWT}`);
    expect(output).not.toContain('notarealpayload');
    expect(output).toContain('[redacted');
  });

  test('strips a long opaque session id', () => {
    const output = redact(`session_id=${FAKE_SESSION_ID}`);
    expect(output).not.toContain(FAKE_SESSION_ID);
    expect(output).toContain('[redacted');
  });

  test('strips explicitly labelled secrets', () => {
    for (const line of [
      'csrf: abc123def',
      'password=hunter2',
      'Authorization: Bearer shorttoken',
      'cookie: decoda_session=zzz',
    ]) {
      expect(redact(line)).toContain('[redacted]');
    }
    expect(redact('password=hunter2')).not.toContain('hunter2');
  });

  test('leaves ordinary diagnostic text readable', () => {
    // Redaction that eats the message defeats the point of capturing it.
    const output = redact('Failed to load resource: the server responded with a status of 504');
    expect(output).toContain('Failed to load resource');
    expect(output).toContain('504');
  });
});

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
  flagRoutes: (flags: Record<string, number>) => string[];
  flagState: (flags: Record<string, number>, route: string, flagName: string) => string | null;
  otherFlags: (flags: Record<string, number>, route: string) => string | null;
  topCheckpoints: (
    phases: Record<string, number>,
    limit?: number,
  ) => { route: string; phase: string; ms: number }[];
  renderMarkdown: (rows: unknown[], args: { baseUrl: string; apiUrl: string; settleMs: number }) => string;
};

let backendDeltas: Harness['backendDeltas'];
let parseMetrics: Harness['parseMetrics'];
let redact: Harness['redact'];
let sumCounter: Harness['sumCounter'];
let sumPhase: Harness['sumPhase'];
let flagRoutes: Harness['flagRoutes'];
let flagState: Harness['flagState'];
let otherFlags: Harness['otherFlags'];
let topCheckpoints: Harness['topCheckpoints'];
let renderMarkdown: Harness['renderMarkdown'];

test.beforeAll(async () => {
  const harness: Harness = await import(pathToFileURL(HARNESS_PATH).href);
  ({ backendDeltas, parseMetrics, redact, sumCounter, sumPhase } = harness);
  ({ flagRoutes, flagState, otherFlags, topCheckpoints } = harness);
  ({ renderMarkdown } = harness);
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

/**
 * The per-load reporting. The capture is read one load at a time -- "was THIS
 * load a cache hit, and what was slow on it" -- and a view that only sums across
 * loads cannot answer that, because the cold load and the cached load are
 * exactly the two cases being compared.
 */
test.describe('per-load flag and checkpoint reporting', () => {
  test('a flag is reported per route, not collapsed across routes', () => {
    // Both the executive summary and the standalone runtime-status endpoint
    // report runtime_status_cache, and within one load they legitimately
    // disagree: one computes and the other reads the cache it just wrote.
    // Collapsing them would invent one answer where the measurement has two.
    const flags = {
      'ops_dashboard_executive_summary:runtime_status_cache=miss': 1,
      'ops_monitoring_runtime_status:runtime_status_cache=hit': 1,
    };

    expect(flagState(flags, 'ops_dashboard_executive_summary', 'runtime_status_cache')).toBe('miss');
    expect(flagState(flags, 'ops_monitoring_runtime_status', 'runtime_status_cache')).toBe('hit');
  });

  test('a route that never reported the flag is null, not a guess', () => {
    const flags = { 'ops_dashboard_executive_summary:runtime_status_cache=hit': 1 };

    // "This route did not report" must not read as "this route reported a miss".
    expect(flagState(flags, 'auth_me', 'runtime_status_cache')).toBeNull();
    expect(flagState({}, 'ops_dashboard_executive_summary', 'response_cache_hit')).toBeNull();
  });

  test('a flag seen more than once in a load keeps its count', () => {
    const flags = { 'ops_monitoring_runtime_status:runtime_status_single_flight=joined': 3 };

    expect(flagState(flags, 'ops_monitoring_runtime_status', 'runtime_status_single_flight')).toBe('joined x3');
  });

  test('two states for one route in one load are both shown', () => {
    const flags = {
      'ops_monitoring_runtime_status:runtime_status_cache=hit': 1,
      'ops_monitoring_runtime_status:runtime_status_cache=miss': 1,
    };

    expect(flagState(flags, 'ops_monitoring_runtime_status', 'runtime_status_cache')).toBe('hit, miss');
  });

  test('routes reporting flags are listed once, sorted', () => {
    const flags = {
      'ops_monitoring_runtime_status:runtime_status_cache=hit': 1,
      'ops_monitoring_runtime_status:rpc_reachable=True': 1,
      'ops_dashboard_executive_summary:response_cache_hit=False': 1,
    };

    expect(flagRoutes(flags)).toEqual([
      'ops_dashboard_executive_summary',
      'ops_monitoring_runtime_status',
    ]);
  });

  test('flags without a column of their own are still reported', () => {
    // rpc_reachable is how a fast rpc_probe_ms is told apart from a skipped one.
    const flags = {
      'ops_monitoring_runtime_status:runtime_status_cache=hit': 1,
      'ops_monitoring_runtime_status:rpc_reachable=True': 1,
      'ops_monitoring_runtime_status:rpc_reachability_source=cached_probe': 1,
    };

    const rest = otherFlags(flags, 'ops_monitoring_runtime_status');
    expect(rest).toContain('rpc_reachable=True');
    expect(rest).toContain('rpc_reachability_source=cached_probe');
    // The headline flag has its own column and must not be duplicated here.
    expect(rest).not.toContain('runtime_status_cache');
  });

  test('checkpoints are ranked slowest first within one load', () => {
    const phases = {
      'ops_monitoring_runtime_status:ckpt.count_open_alerts': 40,
      'ops_monitoring_runtime_status:ckpt.load_targets': 120,
      'ops_monitoring_runtime_status:ckpt.count_assets': 80,
    };

    expect(topCheckpoints(phases).map((entry) => entry.phase)).toEqual([
      'ckpt.load_targets',
      'ckpt.count_assets',
      'ckpt.count_open_alerts',
    ]);
  });

  test('only checkpoints are ranked, and the route is kept', () => {
    const phases = {
      'ops_dashboard_executive_summary:build_summary_ms': 900,
      'ops_dashboard_executive_summary:ckpt.load_targets': 120,
      'ops_monitoring_runtime_status:ckpt.load_targets': 60,
    };

    const ranked = topCheckpoints(phases);
    // build_summary_ms is a phase, not a checkpoint, and has its own column.
    expect(ranked).toHaveLength(2);
    // The same checkpoint under two routes stays two rows: they are two
    // separate computations within the load, not one to be merged.
    expect(ranked[0]).toEqual({
      route: 'ops_dashboard_executive_summary',
      phase: 'ckpt.load_targets',
      ms: 120,
    });
    expect(ranked[1].route).toBe('ops_monitoring_runtime_status');
  });

  test('the ranking is capped at the requested limit', () => {
    const phases: Record<string, number> = {};
    for (let index = 0; index < 25; index += 1) {
      phases[`ops_monitoring_runtime_status:ckpt.query_${index}`] = index;
    }

    expect(topCheckpoints(phases, 10)).toHaveLength(10);
    expect(topCheckpoints(phases, 10)[0].ms).toBe(24);
  });

  test('a load with no checkpoints ranks nothing rather than throwing', () => {
    expect(topCheckpoints({})).toEqual([]);
  });
});

/**
 * The report itself. Correct helpers wired in wrongly still produce a table that
 * looks complete and is wrong, which is the failure mode this whole artifact
 * exists to avoid -- so assert on the rendered document, not only its parts.
 */
test.describe('rendered report', () => {
  const ARGS = { baseUrl: 'https://app.example.com', apiUrl: 'https://api.example.com', settleMs: 0 };

  const ROW = {
    load: 1,
    skeletonDismissed: true,
    wallClockMs: 5120,
    marks: { 'auth.me.response': 800, 'dashboard.skeleton.dismissed': 4950 },
    criticalPath: null,
    network: { '/api/auth/me': 420, '/api/dashboard/executive-summary': 3100 },
    backend: {
      phases: {
        'ops_dashboard_executive_summary:runtime_status_ms': 2600,
        'ops_dashboard_executive_summary:build_summary_ms': 900,
        'ops_monitoring_runtime_status:ckpt.load_targets': 310,
        'ops_dashboard_executive_summary:ckpt.count_assets': 120,
      },
      counters: {
        'ops_dashboard_executive_summary:db_connect_count': 4,
        'ops_dashboard_executive_summary:db_query_count': 44,
      },
      flags: {
        'ops_dashboard_executive_summary:runtime_status_cache=miss': 1,
        'ops_dashboard_executive_summary:response_cache_hit=False': 1,
        'ops_monitoring_runtime_status:runtime_status_cache=hit': 1,
        'ops_monitoring_runtime_status:rpc_reachable=True': 1,
      },
      requests: { ops_dashboard_executive_summary: 3600 },
    },
    consoleErrors: [],
  };

  test('network legs are rendered per load', () => {
    const markdown = renderMarkdown([ROW], ARGS);

    expect(markdown).toContain('## Network legs');
    expect(markdown).toContain('/api/dashboard/executive-summary');
    expect(markdown).toContain('3100');
  });

  test('both routes appear with their own cache state', () => {
    const markdown = renderMarkdown([ROW], ARGS);

    // The two routes disagreed within this load; the report must show both,
    // on their own rows, rather than collapsing to a single verdict.
    const rows = markdown.split('\n');
    expect(rows.some((line) => line.includes('| ops_dashboard_executive_summary |') && line.includes('miss'))).toBe(true);
    expect(rows.some((line) => line.includes('| ops_monitoring_runtime_status |') && line.includes('hit'))).toBe(true);
    // A flag with no column of its own is still reported.
    expect(markdown).toContain('rpc_reachable=True');
  });

  test('checkpoints are rendered per load as well as summed', () => {
    const markdown = renderMarkdown([ROW], ARGS);

    expect(markdown).toContain('top 10 per load');
    expect(markdown).toContain('summed across loads');
    expect(markdown).toContain('ckpt.load_targets');
  });

  test('an unmeasured value renders as a dash, never as zero', () => {
    const empty = {
      ...ROW,
      load: 2,
      marks: {},
      network: {},
      backend: { phases: {}, counters: {}, flags: {}, requests: {} },
    };

    const markdown = renderMarkdown([empty], ARGS);
    // "Not measured" must stay visibly distinct from "measured as zero".
    expect(markdown).toContain('—');
    expect(markdown).toContain('is a fact about');
  });
});

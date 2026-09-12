#!/usr/bin/env node
/**
 * Capture N consecutive /dashboard loads and write one timing artifact.
 *
 * Why this exists: the dashboard's remaining latency was being argued about from
 * numbers that lived only in a commit message. The instrumentation emits a
 * per-request summary (`dashboard_timing route=...`) and a browser critical path
 * (`[dashboard-perf] critical path`), but nothing ever collected them, so every
 * claim about where the seconds go was unfalsifiable.
 *
 * Measurement only. It drives the real UI and reads the API's own /metrics; it
 * changes no application behaviour and writes nothing to the product database.
 *
 * Backend numbers come from /metrics rather than the API's stdout, so this works
 * against a deployed environment with no log access. /metrics is a process-local
 * in-memory registry, which means two things worth stating plainly:
 *
 *   - the API must be a SINGLE process (Procfile runs uvicorn with no --workers).
 *     Against several replicas behind a load balancer the deltas are wrong, not
 *     merely noisy, because the scrape and the request can hit different ones.
 *   - concurrent traffic from anyone else lands in the same counters. Capture on
 *     a quiet environment, or treat the backend columns as an upper bound.
 *
 * Usage:
 *   DECODA_CAPTURE_EMAIL=you@example.com \
 *   DECODA_CAPTURE_PASSWORD='...' \
 *   node scripts/capture-dashboard-timings.mjs \
 *     --base-url https://app.example.com \
 *     --api-url  https://api.example.com \
 *     --loads 5
 *
 * Capturing the cached path and the cold path:
 *
 *   # cold -- gap exceeds RUNTIME_STATUS_CACHE_TTL_SECONDS (default 15), so each
 *   # load recomputes. Idle ~20s first: --settle only sleeps BETWEEN loads, so
 *   # load 1 has no lead-in. Run this one first, so it does not inherit a cache.
 *   ... --loads 5 --settle 20 --out artifacts/dashboard-timing/out-of-ttl
 *
 *   # cached -- over-sample and keep the loads whose measured
 *   # `runtime_status_cache` reads `hit`. A load that itself takes ~5s walks the
 *   # gap forward, so --settle 0 alone does not keep 5 loads inside a 15s TTL.
 *   ... --loads 10 --settle 0 --out artifacts/dashboard-timing/in-ttl
 *
 * A load is inside the TTL because the flag says `hit`, never because of the gap
 * we asked for. The report prints that flag per load and per route for exactly
 * this reason: an assumed cache state is not a measured one.
 *
 * There is deliberately no --password flag: a password on the command line
 * lands in shell history and in the process table, where any other user on the
 * box can read it. The password comes from DECODA_CAPTURE_PASSWORD or, when that
 * is unset, an interactive prompt with echo disabled. --storage-state <file>
 * from a previous sign-in skips credentials entirely and is the better option
 * for anything repeated.
 *
 * Nothing secret is written to the artifacts: the captured values are durations,
 * counts and HTTP status codes. Request/response bodies, headers, cookies and
 * query strings are never read, and the credentials are stripped from the run
 * metadata. Console output captured from failed loads is redacted for
 * token-shaped strings before it is stored.
 */

import { chromium } from 'playwright';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Backend phases worth a column of their own, in critical-path order. */
const HEADLINE_PHASES = [
  'runtime_status_ms',
  'rpc_probe_ms',
  'auth_scope_ms',
  'response_cache_lookup_ms',
  'build_summary_ms',
  'db_connect_ms',
  'db_query_ms',
];

/** Browser marks worth a column of their own, in critical-path order. */
const HEADLINE_MARKS = [
  'runtime-config.response',
  'auth.csrf.response',
  'auth.me.response',
  'session.ready',
  'workspace.ready',
  'dashboard.mount',
  'summary.request.start',
  'summary.response',
  'runtime-status.response',
  'dashboard.skeleton.dismissed',
];

/**
 * Flags that get a column of their own rather than being folded into the
 * catch-all list. These are the three that change how a duration should be
 * read: a small `runtime_status_ms` means nothing until you know whether it was
 * a cache hit, whether this caller led or joined the single-flight, and whether
 * the response cache answered.
 */
const HEADLINE_FLAGS = [
  'runtime_status_cache',
  'runtime_status_single_flight',
  'response_cache_hit',
];

/**
 * Network legs worth a column, in the order the browser issues them.
 * `/api/runtime-config` blocks the auth pair, which in turn blocks the summary,
 * so a slow leg early here explains far more than its own duration.
 */
const HEADLINE_NETWORK = [
  '/api/runtime-config',
  '/api/auth/csrf',
  '/api/auth/me',
  '/api/dashboard/executive-summary',
  '/api/ops/monitoring/runtime-status',
];

function parseArgs(argv) {
  const args = {
    loads: 5,
    baseUrl: process.env.DECODA_CAPTURE_BASE_URL ?? 'http://127.0.0.1:3000',
    apiUrl: process.env.DECODA_CAPTURE_API_URL ?? '',
    email: process.env.DECODA_CAPTURE_EMAIL ?? '',
    password: process.env.DECODA_CAPTURE_PASSWORD ?? '',
    storageState: '',
    out: path.join(REPO_ROOT, 'artifacts', 'dashboard-timing'),
    settleMs: 0,
    timeoutMs: 120_000,
    headed: false,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    switch (flag) {
      case '--loads': args.loads = Number(value); i += 1; break;
      case '--base-url': args.baseUrl = value; i += 1; break;
      case '--api-url': args.apiUrl = value; i += 1; break;
      case '--email': args.email = value; i += 1; break;
      case '--password':
        throw new Error(
          '--password is not supported: a password on the command line is visible in '
          + 'shell history and the process table. Set DECODA_CAPTURE_PASSWORD, or omit '
          + 'it and answer the prompt, or use --storage-state.',
        );
      case '--storage-state': args.storageState = value; i += 1; break;
      case '--out': args.out = value; i += 1; break;
      // Seconds between loads. >15 (the RUNTIME_STATUS_CACHE_TTL_SECONDS default)
      // forces a cold computation each time. 0 measures the cached path, but does
      // NOT guarantee every load lands inside the TTL: a load that itself takes
      // ~5s walks the gap forward, so load 4 or 5 can expire it. Over-sample with
      // a larger --loads and select the loads whose measured `runtime_status_cache`
      // reads `hit` -- the flag is the fact, the gap is only an intention.
      case '--settle': args.settleMs = Number(value) * 1000; i += 1; break;
      case '--timeout': args.timeoutMs = Number(value) * 1000; i += 1; break;
      case '--headed': args.headed = true; break;
      case '--help':
        console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0]);
        process.exit(0);
        break;
      default:
        throw new Error(`Unknown argument: ${flag}`);
    }
  }

  if (!Number.isInteger(args.loads) || args.loads < 1) {
    throw new Error('--loads must be a positive integer');
  }
  if (!args.storageState && !args.email) {
    throw new Error(
      'Provide --email (with DECODA_CAPTURE_PASSWORD or the interactive prompt), '
      + 'or --storage-state from a previous sign-in.',
    );
  }
  return args;
}

/** Read a password from the terminal without echoing it. */
async function promptForPassword(email) {
  if (!process.stdin.isTTY) {
    throw new Error(
      'DECODA_CAPTURE_PASSWORD is unset and stdin is not a terminal, so there is '
      + 'nowhere safe to read the password from. Set the environment variable or '
      + 'use --storage-state.',
    );
  }
  const readline = await import('node:readline');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  try {
    return await new Promise((resolve) => {
      // Suppress echo: the prompt is written once, then keystrokes are swallowed.
      const onData = () => rl.output.write('');
      rl.input.on('data', onData);
      rl.output.write(`Password for ${email}: `);
      rl._writeToOutput = () => undefined;
      rl.question('', (answer) => {
        rl.input.off('data', onData);
        rl.output.write('\n');
        resolve(answer);
      });
    });
  } finally {
    rl.close();
  }
}

/**
 * Strip token-shaped strings out of text captured from the page.
 *
 * Console errors are the one place arbitrary application text reaches the
 * artifact. A stack trace or an error message could carry a bearer token, a CSRF
 * value or a session id, and the artifact is meant to be pasteable.
 */
export function redact(text) {
  return String(text)
    // JWTs (three base64url segments).
    .replace(/\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, '[redacted-jwt]')
    // Long opaque hex/base64 runs: session ids, CSRF values, signatures.
    .replace(/\b[A-Fa-f0-9]{32,}\b/g, '[redacted-hex]')
    .replace(/\b[A-Za-z0-9+/_-]{40,}={0,2}\b/g, '[redacted-token]')
    // Anything explicitly labelled.
    .replace(/((?:token|password|secret|cookie|authorization|csrf)["'\s:=]+)\S+/gi, '$1[redacted]');
}

/**
 * Parse the API's Prometheus text into a flat map of `name{labels} -> number`.
 * The exposition here is plain `name{labels} value` lines with no HELP/TYPE.
 */
export function parseMetrics(text) {
  const series = new Map();
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }
    const splitAt = line.lastIndexOf(' ');
    if (splitAt === -1) {
      continue;
    }
    const key = line.slice(0, splitAt);
    const value = Number(line.slice(splitAt + 1));
    if (Number.isFinite(value)) {
      series.set(key, value);
    }
  }
  return series;
}

async function scrapeMetrics(apiUrl) {
  if (!apiUrl) {
    return new Map();
  }
  try {
    const response = await fetch(`${apiUrl.replace(/\/$/, '')}/metrics`, { cache: 'no-store' });
    if (!response.ok) {
      console.warn(`  ! /metrics returned HTTP ${response.status}; backend columns will be blank`);
      return new Map();
    }
    return parseMetrics(await response.text());
  } catch (error) {
    console.warn(`  ! /metrics unreachable (${error.message}); backend columns will be blank`);
    return new Map();
  }
}

function delta(before, after, key) {
  const value = (after.get(key) ?? 0) - (before.get(key) ?? 0);
  return Number(value.toFixed(6));
}

/** Every series whose name matches and whose labels satisfy `match`. */
function seriesMatching(metrics, name, match) {
  const found = [];
  for (const key of metrics.keys()) {
    if (!key.startsWith(`${name}{`) && key !== name) {
      continue;
    }
    const labels = {};
    const labelPart = key.slice(name.length).replace(/^\{/, '').replace(/\}$/, '');
    for (const pair of labelPart.match(/(\w+)="((?:[^"\\]|\\.)*)"/g) ?? []) {
      const [, k, v] = pair.match(/(\w+)="((?:[^"\\]|\\.)*)"/);
      labels[k] = v.replace(/\\"/g, '"').replace(/\\\\/g, '\\');
    }
    if (match(labels)) {
      found.push({ key, labels });
    }
  }
  return found;
}

/** Backend phase/counter/flag deltas attributable to one load. */
export function backendDeltas(before, after) {
  const phases = {};
  for (const { key, labels } of seriesMatching(after, 'decoda_dashboard_phase_seconds_sum', () => true)) {
    const seconds = delta(before, after, key);
    if (seconds > 0) {
      const name = `${labels.route}:${labels.phase}`;
      phases[name] = Number((seconds * 1000).toFixed(2));
    }
  }

  const counters = {};
  for (const { key, labels } of seriesMatching(after, 'decoda_dashboard_counter_total', () => true)) {
    const count = delta(before, after, key);
    if (count > 0) {
      counters[`${labels.route}:${labels.counter}`] = count;
    }
  }

  const flags = {};
  for (const { key, labels } of seriesMatching(after, 'decoda_dashboard_flag_total', () => true)) {
    const count = delta(before, after, key);
    if (count > 0) {
      flags[`${labels.route}:${labels.flag}=${labels.state}`] = count;
    }
  }

  const requests = {};
  for (const { key, labels } of seriesMatching(after, 'decoda_dashboard_request_seconds_sum', () => true)) {
    const seconds = delta(before, after, key);
    if (seconds > 0) {
      requests[labels.route] = Number((seconds * 1000).toFixed(2));
    }
  }

  return { phases, counters, flags, requests };
}

/** Sum a phase across routes, e.g. db_connect_ms wherever it was recorded. */
export function sumPhase(phases, phaseName) {
  let total = 0;
  let seen = false;
  for (const [key, value] of Object.entries(phases)) {
    if (key.endsWith(`:${phaseName}`)) {
      total += value;
      seen = true;
    }
  }
  return seen ? Number(total.toFixed(2)) : null;
}

export function sumCounter(counters, counterName) {
  let total = 0;
  let seen = false;
  for (const [key, value] of Object.entries(counters)) {
    if (key.endsWith(`:${counterName}`)) {
      total += value;
      seen = true;
    }
  }
  return seen ? total : null;
}

/** Every route that reported at least one flag in this load. */
export function flagRoutes(flags) {
  const routes = new Set();
  for (const key of Object.keys(flags)) {
    routes.add(key.split(':')[0]);
  }
  return [...routes].sort();
}

/**
 * The state of one flag for ONE route, e.g. runtime_status_cache on
 * ops_dashboard_executive_summary.
 *
 * Deliberately not summed across routes. Both the executive summary and the
 * standalone runtime-status endpoint report `runtime_status_cache`, and they can
 * legitimately disagree within a single load -- one computes and the other joins
 * or reads the cache it just wrote. Collapsing them would invent a single answer
 * where the measurement has two.
 */
export function flagState(flags, route, flagName) {
  const prefix = `${route}:${flagName}=`;
  const states = [];
  for (const [key, count] of Object.entries(flags)) {
    if (!key.startsWith(prefix)) {
      continue;
    }
    const state = key.slice(prefix.length);
    states.push(count > 1 ? `${state} x${count}` : state);
  }
  return states.length > 0 ? states.sort().join(', ') : null;
}

/** Flags for one route that have no column of their own. */
export function otherFlags(flags, route) {
  const rest = [];
  for (const [key, count] of Object.entries(flags)) {
    if (!key.startsWith(`${route}:`)) {
      continue;
    }
    const name = key.slice(route.length + 1).split('=')[0];
    if (HEADLINE_FLAGS.includes(name)) {
      continue;
    }
    const shown = key.slice(route.length + 1);
    rest.push(count > 1 ? `${shown} x${count}` : shown);
  }
  return rest.length > 0 ? rest.sort().join(', ') : null;
}

/**
 * The slowest `ckpt.*` phases within ONE load, slowest first.
 *
 * The summed-across-loads view answers "what is generally slow"; this answers
 * "what was slow on the cold load specifically", which is the one that differs
 * between a cache hit and a miss.
 */
export function topCheckpoints(phases, limit = 10) {
  const checkpoints = [];
  for (const [key, ms] of Object.entries(phases)) {
    const route = key.split(':')[0];
    const phase = key.slice(route.length + 1);
    if (phase.startsWith('ckpt.')) {
      checkpoints.push({ route, phase, ms });
    }
  }
  return checkpoints.sort((a, b) => b.ms - a.ms).slice(0, limit);
}

async function signIn(context, args) {
  const page = await context.newPage();
  await page.goto(`${args.baseUrl.replace(/\/$/, '')}/sign-in`, { waitUntil: 'domcontentloaded' });
  await page.fill('#si-email', args.email);
  await page.fill('#si-password', args.password);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/sign-in'), { timeout: args.timeoutMs }),
    page.click('button[type="submit"]:has-text("Sign in")').catch(() => page.click('button[type="submit"]')),
  ]);
  await page.close();
}

/** One dashboard load: browser marks, network legs, and backend metric deltas. */
async function captureOneLoad(context, args, loadIndex) {
  const page = await context.newPage();

  // Turn the browser marks on before any app code runs. The build-time
  // NEXT_PUBLIC_DASHBOARD_PERF cannot be flipped in a deployed environment.
  await page.addInitScript(() => {
    window.__DECODA_DASHBOARD_PERF = true;
  });

  const marks = {};
  let criticalPath = null;
  const consoleErrors = [];

  page.on('console', (message) => {
    const text = message.text();
    if (!text.startsWith('[dashboard-perf]')) {
      if (message.type() === 'error') {
        consoleErrors.push(redact(text).slice(0, 300));
      }
      return;
    }
    // Marks are logged as console.info('[dashboard-perf]', mark, detail) and the
    // summary as console.info('[dashboard-perf] critical path', payload).
    Promise.all(message.args().map((arg) => arg.jsonValue().catch(() => null)))
      .then((values) => {
        if (text.startsWith('[dashboard-perf] critical path')) {
          criticalPath = values[1] ?? null;
          return;
        }
        const [, mark, detail] = values;
        if (typeof mark === 'string' && detail && typeof detail.sinceStartMs === 'number') {
          marks[mark] = detail.sinceStartMs;
        }
      })
      .catch(() => undefined);
  });

  const network = {};
  page.on('response', (response) => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith('/api/')) {
      return;
    }
    const request = response.request();
    const timing = request.timing();
    if (timing && timing.responseEnd > 0) {
      network[url.pathname] = Number((timing.responseEnd - timing.startTime).toFixed(1));
    }
  });

  const before = await scrapeMetrics(args.apiUrl);
  const startedAt = Date.now();

  await page.goto(`${args.baseUrl.replace(/\/$/, '')}/dashboard`, {
    waitUntil: 'commit',
    timeout: args.timeoutMs,
  });

  // The skeleton's dismissal is the thing being measured, so wait for the mark
  // rather than for a network-idle heuristic that would include later work.
  let skeletonDismissed = true;
  try {
    await page.waitForFunction(
      () => performance.getEntriesByName('decoda.dashboard.skeleton.dismissed', 'mark').length > 0,
      undefined,
      { timeout: args.timeoutMs },
    );
  } catch {
    skeletonDismissed = false;
    console.warn(`  ! load ${loadIndex}: skeleton never dismissed within ${args.timeoutMs}ms`);
  }

  // Let the console listener drain the marks it is still resolving.
  await page.waitForTimeout(250);

  const wallClockMs = Date.now() - startedAt;
  const after = await scrapeMetrics(args.apiUrl);
  await page.close();

  return {
    load: loadIndex,
    skeletonDismissed,
    wallClockMs,
    marks,
    criticalPath,
    network,
    backend: backendDeltas(before, after),
    consoleErrors,
  };
}

function cell(value) {
  if (value === null || value === undefined) {
    return '—';
  }
  return typeof value === 'number' ? String(value) : String(value);
}

export function renderMarkdown(rows, args) {
  const lines = [];
  lines.push('# Dashboard load timings');
  lines.push('');
  lines.push(`- Captured: ${new Date().toISOString()}`);
  lines.push(`- Base URL: ${args.baseUrl}`);
  lines.push(`- API URL: ${args.apiUrl || '(not scraped)'}`);
  lines.push(`- Loads: ${rows.length}`);
  lines.push(`- Gap between loads: ${args.settleMs / 1000}s`);
  lines.push('');
  lines.push('All values in milliseconds unless named otherwise. `—` means the');
  lines.push('instrumentation recorded nothing for that load, which is a fact about');
  lines.push('the measurement, not a zero.');
  lines.push('');

  lines.push('## Browser critical path (offsets from navigation start)');
  lines.push('');
  lines.push(`| Load | ${HEADLINE_MARKS.join(' | ')} | wall clock |`);
  lines.push(`|---|${HEADLINE_MARKS.map(() => '---:').join('|')}|---:|`);
  for (const row of rows) {
    const cells = HEADLINE_MARKS.map((mark) => cell(row.marks[mark] ?? null));
    lines.push(`| ${row.load} | ${cells.join(' | ')} | ${row.wallClockMs} |`);
  }
  lines.push('');

  lines.push('## Network legs (request duration, browser-side)');
  lines.push('');
  lines.push('Duration of the request itself, unlike the offsets above. The gap');
  lines.push('between a leg here and its backend route total is the proxy + network cost.');
  lines.push('');
  lines.push(`| Load | ${HEADLINE_NETWORK.join(' | ')} |`);
  lines.push(`|---|${HEADLINE_NETWORK.map(() => '---:').join('|')}|`);
  for (const row of rows) {
    const cells = HEADLINE_NETWORK.map((leg) => cell(row.network[leg] ?? null));
    lines.push(`| ${row.load} | ${cells.join(' | ')} |`);
  }
  lines.push('');

  lines.push('## Backend phases (from /metrics deltas)');
  lines.push('');
  lines.push(`| Load | ${HEADLINE_PHASES.join(' | ')} | db_connect_count | db_query_count |`);
  lines.push(`|---|${HEADLINE_PHASES.map(() => '---:').join('|')}|---:|---:|`);
  for (const row of rows) {
    const cells = HEADLINE_PHASES.map((phase) => cell(sumPhase(row.backend.phases, phase)));
    const connects = cell(sumCounter(row.backend.counters, 'db_connect_count'));
    const queries = cell(sumCounter(row.backend.counters, 'db_query_count'));
    lines.push(`| ${row.load} | ${cells.join(' | ')} | ${connects} | ${queries} |`);
  }
  lines.push('');

  lines.push('## Cache and single-flight state (per load, per route)');
  lines.push('');
  lines.push('A load is inside the runtime-status TTL because `runtime_status_cache`');
  lines.push('says `hit`, never because of the gap we asked for.');
  lines.push('');
  lines.push(`| Load | route | ${HEADLINE_FLAGS.join(' | ')} | other flags |`);
  lines.push(`|---|---|${HEADLINE_FLAGS.map(() => '---').join('|')}|---|`);
  for (const row of rows) {
    const routes = flagRoutes(row.backend.flags);
    if (routes.length === 0) {
      lines.push(`| ${row.load} | — | ${HEADLINE_FLAGS.map(() => '—').join(' | ')} | — |`);
      continue;
    }
    for (const route of routes) {
      const cells = HEADLINE_FLAGS.map((flagName) => cell(flagState(row.backend.flags, route, flagName)));
      lines.push(`| ${row.load} | ${route} | ${cells.join(' | ')} | ${cell(otherFlags(row.backend.flags, route))} |`);
    }
  }
  lines.push('');

  lines.push('## Slowest runtime-status checkpoints (summed across loads)');
  lines.push('');
  const checkpointTotals = new Map();
  for (const row of rows) {
    for (const [key, value] of Object.entries(row.backend.phases)) {
      const phase = key.split(':').slice(1).join(':');
      if (phase.startsWith('ckpt.')) {
        checkpointTotals.set(phase, (checkpointTotals.get(phase) ?? 0) + value);
      }
    }
  }
  if (checkpointTotals.size === 0) {
    lines.push('No `ckpt.*` phases recorded.');
  } else {
    lines.push('| Checkpoint | total ms | mean ms/load |');
    lines.push('|---|---:|---:|');
    for (const [phase, total] of [...checkpointTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)) {
      lines.push(`| ${phase} | ${total.toFixed(2)} | ${(total / rows.length).toFixed(2)} |`);
    }
  }
  lines.push('');

  lines.push('## Slowest runtime-status checkpoints (top 10 per load)');
  lines.push('');
  lines.push('| Load | # | route | checkpoint | ms |');
  lines.push('|---|---:|---|---|---:|');
  for (const row of rows) {
    const checkpoints = topCheckpoints(row.backend.phases, 10);
    if (checkpoints.length === 0) {
      lines.push(`| ${row.load} | — | — | — | — |`);
      continue;
    }
    checkpoints.forEach((entry, index) => {
      lines.push(`| ${row.load} | ${index + 1} | ${entry.route} | ${entry.phase} | ${entry.ms.toFixed(2)} |`);
    });
  }
  lines.push('');

  lines.push('## Per-request totals by route');
  lines.push('');
  lines.push('| Load | route | total ms |');
  lines.push('|---|---|---:|');
  for (const row of rows) {
    const entries = Object.entries(row.backend.requests).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) {
      lines.push(`| ${row.load} | — | — |`);
      continue;
    }
    for (const [route, ms] of entries) {
      lines.push(`| ${row.load} | ${route} | ${ms} |`);
    }
  }
  lines.push('');

  const incomplete = rows.filter((row) => !row.skeletonDismissed);
  if (incomplete.length > 0) {
    lines.push(`> ${incomplete.length} load(s) never dismissed the skeleton within the timeout.`);
    lines.push('');
  }

  return lines.join('\n');
}

async function main() {
  const args = parseArgs(process.argv);
  fs.mkdirSync(args.out, { recursive: true });

  const browser = await chromium.launch({ headless: !args.headed });
  const context = await browser.newContext(
    args.storageState ? { storageState: args.storageState } : undefined,
  );

  try {
    if (!args.storageState) {
      if (!args.password) {
        args.password = await promptForPassword(args.email);
      }
      if (!args.password) {
        throw new Error('No password supplied; cannot sign in.');
      }
      console.log('Signing in…');
      await signIn(context, args);
    }

    const rows = [];
    for (let loadIndex = 1; loadIndex <= args.loads; loadIndex += 1) {
      if (loadIndex > 1 && args.settleMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, args.settleMs));
      }
      console.log(`Load ${loadIndex}/${args.loads}…`);
      const row = await captureOneLoad(context, args, loadIndex);
      console.log(
        `  skeleton dismissed at ${cell(row.marks['dashboard.skeleton.dismissed'] ?? null)}ms`
        + ` (wall clock ${row.wallClockMs}ms)`,
      );
      rows.push(row);
    }

    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const jsonPath = path.join(args.out, `dashboard-timings-${stamp}.json`);
    const markdownPath = path.join(args.out, `dashboard-timings-${stamp}.md`);

    // Explicitly rebuild the metadata rather than deleting keys from `args`:
    // a future flag would otherwise be included by default, and the artifact is
    // meant to be pasteable into a conversation.
    const runMetadata = {
      baseUrl: args.baseUrl,
      apiUrl: args.apiUrl,
      loads: args.loads,
      settleSeconds: args.settleMs / 1000,
      timeoutSeconds: args.timeoutMs / 1000,
      usedStorageState: Boolean(args.storageState),
    };
    fs.writeFileSync(
      jsonPath,
      `${JSON.stringify({ capturedAt: new Date().toISOString(), run: runMetadata, rows }, null, 2)}\n`,
    );
    fs.writeFileSync(markdownPath, `${renderMarkdown(rows, args)}\n`);

    console.log(`\nWrote ${path.relative(REPO_ROOT, markdownPath)}`);
    console.log(`Wrote ${path.relative(REPO_ROOT, jsonPath)}`);
  } finally {
    await context.close();
    await browser.close();
  }
}

// Only drive a capture when run as a script. Importing this module (the tests
// do) must not launch a browser or try to sign in.
const invokedDirectly = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

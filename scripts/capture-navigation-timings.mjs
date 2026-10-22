#!/usr/bin/env node
/**
 * Capture page-navigation timings across the primary Guard screens.
 *
 * `capture-dashboard-timings.mjs` answers "where do the dashboard's seconds go"
 * for one route, with backend phase deltas. This answers the wider question an
 * operator feels: how long does each screen take to become usable, both as a
 * fresh document load and as a sidebar (client-side) navigation, and how much
 * the shell polls while a screen is simply open.
 *
 * Measurement only. It drives the real UI in Chromium and reads the browser's
 * own Resource Timing and the `decoda.*` marks from `app/dashboard-perf.ts`. It
 * changes no application behaviour and writes nothing to the product database:
 * every request it causes is a GET a person opening the same screens would cause.
 *
 * What each column means (all offsets in ms from the navigation start, or from
 * the click for a sidebar navigation):
 *
 *   gate      `decoda.workspace.ready` -- the auth gate stopped replacing the
 *             page with "Loading workspace…". Nothing below it can start earlier.
 *   settled   the last /api/* response that ended before the network went
 *             quiet. Long-lived streams (/api/stream/*) are excluded: they never
 *             end by design and would make every load look infinite.
 *   ready     the last moment a loading indicator was visible in <main>
 *             (role=status/aria-busy, or text such as "Loading…"), sampled
 *             every 25ms. A screen with nothing loading reads its first sample.
 *   api       number of /api/* requests the load issued.
 *   dup       requests for an identical path+query issued more than once in the
 *             same load -- the duplicate-fetch signal.
 *   commit    (sidebar only) click to URL change. Each sidebar click follows a
 *             --hover-ms pointer rest on the link (default 100ms), the way a
 *             person clicks; the clock starts at the click, not the hover.
 *
 * Auth: `--storage-state <file>` from a previous sign-in, or, for a disposable
 * local stack only, `DECODA_CAPTURE_SESSION_TOKEN` in the environment (set as
 * the HttpOnly `decoda_session` cookie). Nothing secret is written to the
 * artifact: only paths without query strings, durations, counts and statuses.
 *
 * Usage:
 *   node scripts/capture-navigation-timings.mjs --base-url http://127.0.0.1:3000 \
 *     --loads 3 --poll-seconds 120 --latency-ms 150 --out artifacts/navigation-timing/before
 *
 * --steady-routes (default /dashboard,/incidents) are each held open for
 * --poll-seconds, counting every request the shell and the screen make.
 */

import { chromium } from 'playwright';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** The sidebar's screens, in workflow order (app/product-nav.ts). */
export const DEFAULT_ROUTES = [
  '/dashboard',
  '/assets',
  '/monitoring-sources',
  '/threat',
  '/alerts',
  '/incidents',
  '/response-actions',
  '/evidence',
  '/integrations',
  '/settings',
  '/system-health',
];

const STREAM_PATH = /^\/api\/stream\//;
const LOADING_TEXT = /\b(loading|validating your session|preparing your workspace)\b/i;

function parseArgs(argv) {
  const args = {
    baseUrl: process.env.DECODA_CAPTURE_BASE_URL || 'http://127.0.0.1:3000',
    loads: 3,
    routes: DEFAULT_ROUTES,
    pollSeconds: 60,
    steadyRoutes: ['/dashboard', '/incidents'],
    quietMs: 1500,
    timeoutMs: 45_000,
    latencyMs: 0,
    hoverMs: 100,
    storageState: null,
    out: null,
    label: 'capture',
  };
  for (let i = 2; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    switch (flag) {
      case '--base-url': args.baseUrl = value; i += 1; break;
      case '--loads': args.loads = Number(value); i += 1; break;
      case '--routes': args.routes = value.split(',').map((r) => r.trim()).filter(Boolean); i += 1; break;
      case '--poll-seconds': args.pollSeconds = Number(value); i += 1; break;
      // Screens to sit on for --poll-seconds each, counting what they request.
      case '--steady-routes': args.steadyRoutes = value.split(',').map((r) => r.trim()).filter(Boolean); i += 1; break;
      case '--quiet-ms': args.quietMs = Number(value); i += 1; break;
      case '--timeout': args.timeoutMs = Number(value) * 1000; i += 1; break;
      // Round-trip latency added to EVERY request via Chromium's network
      // emulation. A local stack answers in ~10-60ms, which hides what each
      // serial round trip costs a browser that is 100-200ms from the edge.
      case '--latency-ms': args.latencyMs = Number(value); i += 1; break;
      // Pointer-on-link time before each sidebar click (0 = click immediately).
      case '--hover-ms': args.hoverMs = Number(value); i += 1; break;
      case '--storage-state': args.storageState = value; i += 1; break;
      case '--out': args.out = value; i += 1; break;
      case '--label': args.label = value; i += 1; break;
      default:
        throw new Error(`Unknown flag ${flag}`);
    }
  }
  if (!Number.isInteger(args.loads) || args.loads < 1) throw new Error('--loads must be a positive integer');
  if (!args.storageState && !process.env.DECODA_CAPTURE_SESSION_TOKEN) {
    throw new Error('Provide --storage-state, or DECODA_CAPTURE_SESSION_TOKEN for a disposable local stack.');
  }
  return args;
}

/**
 * Runs in the page before any app code. Raises the Resource Timing buffer (the
 * default 250 entries is exhausted by a long poll window) and samples whether a
 * loading indicator is visible in <main>.
 */
function initScript(loadingPattern) {
  const pattern = new RegExp(loadingPattern, 'i');
  try { performance.setResourceTimingBufferSize(10_000); } catch { /* not load-bearing */ }
  const state = { lastLoadingAt: null, firstSampleAt: null, epoch: 0 };
  window.__navCapture = state;
  const sample = () => {
    const main = document.querySelector('main');
    const now = performance.now();
    if (state.firstSampleAt === null && main) state.firstSampleAt = now;
    let loading = !main;
    if (main) {
      const busy = main.querySelector('[aria-busy="true"], [class*="skeleton" i], [data-loading="true"]');
      const text = main.innerText || '';
      loading = Boolean(busy) || pattern.test(text.slice(0, 4000));
    }
    if (loading) state.lastLoadingAt = now;
  };
  setInterval(sample, 25);
}

function apiPath(url) {
  try {
    const parsed = new URL(url);
    return parsed.pathname;
  } catch {
    return url;
  }
}

/** Waits until no non-stream /api/ request is in flight and none started for `quietMs`. */
async function waitForQuiet(page, tracker, quietMs, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (tracker.inflight.size === 0 && Date.now() - tracker.lastActivity >= quietMs) return true;
    await page.waitForTimeout(100);
  }
  return false;
}

function attachTracker(page) {
  const tracker = { inflight: new Set(), lastActivity: Date.now(), statuses: [] };
  page.on('request', (request) => {
    const p = apiPath(request.url());
    if (!p.startsWith('/api/') || STREAM_PATH.test(p)) return;
    tracker.inflight.add(request);
    tracker.lastActivity = Date.now();
  });
  const done = (request, status) => {
    if (!tracker.inflight.delete(request)) return;
    tracker.lastActivity = Date.now();
    tracker.statuses.push({ path: apiPath(request.url()), status });
  };
  // Settled on the response HEADERS, not 'requestfinished': a screen that does
  // not read the body of a non-OK response leaves Chromium's body stream open,
  // and 'requestfinished' then never fires, which would read as a hung request.
  // The settled column still comes from Resource Timing's responseEnd.
  page.on('response', (response) => done(response.request(), response.status()));
  page.on('requestfailed', (request) => done(request, 'failed'));
  return tracker;
}

/** Reads resource entries and marks recorded after `sinceMs`, re-based onto it. */
async function readWindow(page, sinceMs) {
  return page.evaluate((since) => {
    const resources = performance.getEntriesByType('resource')
      .filter((entry) => entry.startTime >= since - 1)
      .map((entry) => {
        let pathname = entry.name;
        let search = '';
        try { const u = new URL(entry.name); pathname = u.pathname; search = u.search; } catch { /* keep raw */ }
        return {
          path: pathname,
          // Only a stable fingerprint of the query: enough to tell identical
          // requests apart from different ones, without storing the values.
          queryKey: search ? String(search.split('&').length) + ':' + search.length : '',
          start: Number((entry.startTime - since).toFixed(1)),
          end: Number((entry.responseEnd - since).toFixed(1)),
          initiator: entry.initiatorType,
        };
      })
      .filter((entry) => entry.path.startsWith('/api/'));
    const marks = {};
    for (const entry of performance.getEntriesByType('mark')) {
      if (entry.name.startsWith('decoda.') && entry.startTime >= since - 1) {
        marks[entry.name.slice('decoda.'.length)] = Number((entry.startTime - since).toFixed(1));
      }
    }
    const paint = {};
    if (since === 0) {
      for (const entry of performance.getEntriesByType('paint')) paint[entry.name] = Number(entry.startTime.toFixed(1));
      const nav = performance.getEntriesByType('navigation')[0];
      if (nav) {
        paint.ttfb = Number(nav.responseStart.toFixed(1));
        paint.domContentLoaded = Number(nav.domContentLoadedEventEnd.toFixed(1));
      }
    }
    const capture = window.__navCapture || {};
    const lastLoading = typeof capture.lastLoadingAt === 'number' ? capture.lastLoadingAt - since : null;
    const firstSample = typeof capture.firstSampleAt === 'number' ? Math.max(0, capture.firstSampleAt - since) : null;
    return { resources, marks, paint, lastLoadingMs: lastLoading !== null && lastLoading >= 0 ? Number(lastLoading.toFixed(1)) : firstSample };
  }, sinceMs);
}

function summarise(window) {
  const nonStream = window.resources.filter((r) => !STREAM_PATH.test(r.path));
  const settledMs = nonStream.length ? Math.max(...nonStream.map((r) => r.end)) : 0;
  const counts = new Map();
  for (const r of window.resources) {
    const key = `${r.path}${r.queryKey ? `?${r.queryKey}` : ''}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const duplicates = [...counts.entries()].filter(([, n]) => n > 1).map(([key, n]) => ({ key, n }));
  const byPath = {};
  for (const r of window.resources) byPath[r.path] = (byPath[r.path] ?? 0) + 1;
  return {
    gateMs: window.marks['workspace.ready'] ?? null,
    settledMs: Number(settledMs.toFixed(1)),
    readyMs: window.lastLoadingMs,
    apiCount: window.resources.length,
    duplicateCount: duplicates.reduce((sum, d) => sum + d.n - 1, 0),
    duplicates,
    byPath,
    marks: window.marks,
    paint: window.paint,
    waterfall: window.resources
      .slice()
      .sort((a, b) => a.start - b.start)
      .map((r) => ({ path: r.path, start: r.start, end: r.end })),
  };
}

async function newContext(browser, args) {
  const context = await browser.newContext(args.storageState ? { storageState: args.storageState } : {});
  if (!args.storageState) {
    const url = new URL(args.baseUrl);
    await context.addCookies([{
      name: 'decoda_session',
      value: process.env.DECODA_CAPTURE_SESSION_TOKEN,
      domain: url.hostname,
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
      secure: url.protocol === 'https:',
    }]);
  }
  await context.addInitScript(initScript, LOADING_TEXT.source);
  return context;
}

async function openPage(context, args) {
  const page = await context.newPage();
  if (args.latencyMs > 0) {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions', {
      offline: false, latency: args.latencyMs, downloadThroughput: -1, uploadThroughput: -1,
    });
  }
  return page;
}

async function documentLoad(context, args, route) {
  const page = await openPage(context, args);
  const tracker = attachTracker(page);
  const response = await page.goto(new URL(route, args.baseUrl).toString(), { waitUntil: 'domcontentloaded', timeout: args.timeoutMs });
  const quiet = await waitForQuiet(page, tracker, args.quietMs, args.timeoutMs);
  const finalPath = new URL(page.url()).pathname;
  const window = await readWindow(page, 0);
  await page.close();
  return { route, finalPath, status: response?.status() ?? null, quiet, failed: tracker.statuses.filter((s) => s.status === 'failed' || (typeof s.status === 'number' && s.status >= 500)), ...summarise(window) };
}

/**
 * Sidebar navigation: open /dashboard, let it settle, then click each sidebar
 * link in turn. The click is dispatched inside the page so its timestamp and the
 * resource entries share one clock.
 */
async function sidebarNavigation(context, args, routes) {
  const page = await openPage(context, args);
  const tracker = attachTracker(page);
  await page.goto(new URL('/dashboard', args.baseUrl).toString(), { waitUntil: 'domcontentloaded', timeout: args.timeoutMs });
  await waitForQuiet(page, tracker, args.quietMs, args.timeoutMs);
  const results = [];
  let previous = '/dashboard';
  for (const route of routes) {
    if (route === previous) continue;
    const selector = `nav[aria-label="Product navigation"] a[href="${route}"]`;
    const link = await page.$(selector);
    if (!link) {
      results.push({ route, from: previous, error: 'sidebar link not found' });
      continue;
    }
    // A person's pointer rests on a link before the click lands. Hover first,
    // for the same lead time on every run, so a screen that starts work on
    // intent is measured the way it is used -- and one that does not is not
    // penalised, because the clock below starts at the click either way.
    await link.hover();
    if (args.hoverMs > 0) await page.waitForTimeout(args.hoverMs);
    // The quiet window restarts at the click: without this the previous
    // screen's silence satisfies it before the new screen has asked for anything.
    tracker.lastActivity = Date.now();
    const clickAt = await page.evaluate((sel) => {
      const at = performance.now();
      if (window.__navCapture) window.__navCapture.lastLoadingAt = null;
      document.querySelector(sel).click();
      return at;
    }, selector);
    await page.waitForURL((url) => url.pathname === route || url.pathname.startsWith(`${route}/`), { timeout: args.timeoutMs }).catch(() => null);
    const urlCommitMs = await page.evaluate((at) => performance.now() - at, clickAt);
    const quiet = await waitForQuiet(page, tracker, args.quietMs, args.timeoutMs);
    const window = await readWindow(page, clickAt);
    results.push({ route, from: previous, quiet, urlCommitMs: Number(urlCommitMs.toFixed(1)), ...summarise(window) });
    previous = route;
  }
  await page.close();
  return results;
}

/** Sit on a screen and count what the shell and page poll. */
async function steadyState(context, args, route) {
  const page = await openPage(context, args);
  const tracker = attachTracker(page);
  await page.goto(new URL(route, args.baseUrl).toString(), { waitUntil: 'domcontentloaded', timeout: args.timeoutMs });
  await waitForQuiet(page, tracker, args.quietMs, args.timeoutMs);
  const since = await page.evaluate(() => performance.now());
  await page.waitForTimeout(args.pollSeconds * 1000);
  const window = await readWindow(page, since);
  await page.close();
  const byPath = {};
  for (const r of window.resources) byPath[r.path] = (byPath[r.path] ?? 0) + 1;
  return { route, seconds: args.pollSeconds, apiCount: window.resources.length, perMinute: Number((window.resources.length / (args.pollSeconds / 60)).toFixed(1)), byPath };
}

function median(values) {
  const nums = values.filter((v) => typeof v === 'number').sort((a, b) => a - b);
  if (!nums.length) return null;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : Number(((nums[mid - 1] + nums[mid]) / 2).toFixed(1));
}

function renderMarkdown(report) {
  const lines = [];
  lines.push(`# Navigation timings — ${report.label}`);
  lines.push('');
  lines.push(`Captured ${report.capturedAt} against ${report.baseUrl}; ${report.loads} load(s) per route, medians shown; added round-trip latency ${report.latencyMs}ms; sidebar clicks follow a ${report.hoverMs}ms hover.`);
  lines.push('');
  lines.push('## Document loads (fresh page, warm static cache)');
  lines.push('');
  lines.push('| route | ttfb | fcp | gate | ready | settled | api | dup |');
  lines.push('|---|---:|---:|---:|---:|---:|---:|---:|');
  for (const row of report.documentSummary) {
    lines.push(`| ${row.route} | ${row.ttfb ?? '—'} | ${row.fcp ?? '—'} | ${row.gate ?? '—'} | ${row.ready ?? '—'} | ${row.settled ?? '—'} | ${row.api ?? '—'} | ${row.dup ?? '—'} |`);
  }
  lines.push('');
  lines.push('## Sidebar navigation (client-side, from the previous screen)');
  lines.push('');
  lines.push('| route | url commit | ready | settled | api | dup |');
  lines.push('|---|---:|---:|---:|---:|---:|');
  for (const row of report.sidebarSummary) {
    lines.push(`| ${row.route} | ${row.urlCommit ?? '—'} | ${row.ready ?? '—'} | ${row.settled ?? '—'} | ${row.api ?? '—'} | ${row.dup ?? '—'} |`);
  }
  lines.push('');
  for (const steady of report.steady) {
    lines.push(`## Steady state (${steady.seconds}s on ${steady.route})`);
    lines.push('');
    lines.push(`${steady.apiCount} requests (${steady.perMinute}/min).`);
    lines.push('');
    lines.push('| path | requests |');
    lines.push('|---|---:|');
    for (const [p, n] of Object.entries(steady.byPath).sort((a, b) => b[1] - a[1])) lines.push(`| ${p} | ${n} |`);
    lines.push('');
  }
  lines.push('## /dashboard waterfall (first document load)');
  lines.push('');
  lines.push('| path | start | end |');
  lines.push('|---|---:|---:|');
  const dash = report.documentLoads.find((l) => l.route === '/dashboard');
  for (const r of dash?.waterfall ?? []) lines.push(`| ${r.path} | ${r.start} | ${r.end} |`);
  lines.push('');
  return lines.join('\n');
}

async function main() {
  const args = parseArgs(process.argv);
  // A pinned Playwright whose bundled browser is not installed can still drive
  // a preinstalled Chromium.
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
  const browser = await chromium.launch(executablePath ? { executablePath } : {});
  try {
    const context = await newContext(browser, args);
    // Warm-up: compile caches and static chunks, so load 1 is not a cold-disk outlier.
    await documentLoad(context, args, '/dashboard');

    const documentLoads = [];
    for (let i = 0; i < args.loads; i += 1) {
      for (const route of args.routes) {
        const result = await documentLoad(context, args, route);
        documentLoads.push({ load: i + 1, ...result });
        process.stdout.write(`doc ${route} #${i + 1}: gate=${result.gateMs} ready=${result.readyMs} settled=${result.settledMs} api=${result.apiCount} dup=${result.duplicateCount}\n`);
      }
    }

    const sidebar = [];
    for (let i = 0; i < args.loads; i += 1) {
      const runs = await sidebarNavigation(context, args, args.routes);
      for (const run of runs) {
        sidebar.push({ load: i + 1, ...run });
        process.stdout.write(`nav ${run.route} #${i + 1}: commit=${run.urlCommitMs} ready=${run.readyMs} settled=${run.settledMs} api=${run.apiCount} dup=${run.duplicateCount}\n`);
      }
    }

    const steady = [];
    if (args.pollSeconds > 0) {
      for (const route of args.steadyRoutes) {
        const result = await steadyState(context, args, route);
        steady.push(result);
        process.stdout.write(`steady ${route}: ${result.apiCount} requests in ${result.seconds}s\n`);
      }
    }
    await context.close();

    const documentSummary = args.routes.map((route) => {
      const rows = documentLoads.filter((l) => l.route === route);
      return {
        route,
        ttfb: median(rows.map((r) => r.paint?.ttfb)),
        fcp: median(rows.map((r) => r.paint?.['first-contentful-paint'])),
        gate: median(rows.map((r) => r.gateMs)),
        ready: median(rows.map((r) => r.readyMs)),
        settled: median(rows.map((r) => r.settledMs)),
        api: median(rows.map((r) => r.apiCount)),
        dup: median(rows.map((r) => r.duplicateCount)),
      };
    });
    const sidebarSummary = args.routes.filter((r) => r !== '/dashboard').map((route) => {
      const rows = sidebar.filter((l) => l.route === route && !l.error);
      return {
        route,
        urlCommit: median(rows.map((r) => r.urlCommitMs)),
        ready: median(rows.map((r) => r.readyMs)),
        settled: median(rows.map((r) => r.settledMs)),
        api: median(rows.map((r) => r.apiCount)),
        dup: median(rows.map((r) => r.duplicateCount)),
      };
    });

    const report = {
      label: args.label,
      capturedAt: new Date().toISOString(),
      baseUrl: args.baseUrl,
      latencyMs: args.latencyMs,
      hoverMs: args.hoverMs,
      loads: args.loads,
      routes: args.routes,
      documentSummary,
      sidebarSummary,
      steady,
      documentLoads,
      sidebar,
    };
    const outDir = path.resolve(REPO_ROOT, args.out ?? path.join('artifacts', 'navigation-timing', args.label));
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(path.join(outDir, 'navigation-timings.json'), `${JSON.stringify(report, null, 2)}\n`);
    fs.writeFileSync(path.join(outDir, 'navigation-timings.md'), `${renderMarkdown(report)}\n`);
    process.stdout.write(`\nWrote ${path.relative(REPO_ROOT, outDir)}/navigation-timings.{json,md}\n`);
  } finally {
    await browser.close();
  }
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
    process.exit(1);
  });
}

export { summarise, median, renderMarkdown };

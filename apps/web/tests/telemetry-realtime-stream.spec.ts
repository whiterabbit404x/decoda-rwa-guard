import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Frontend contract for the real-time telemetry SSE path: a new monitored-wallet
// transfer must appear at the TOP of the Target Telemetry table automatically —
// no manual refresh, tx-hash search, clearing search, or navigation — deduped,
// scoped to the current target, respecting the active search/filter, with a
// truthful degraded indicator + reconnect recovery. Source-inspection style
// (matching tests/telemetry-ordering-and-cache.spec.ts): asserts the page wires the
// stream client, prepends + dedupes, and reports status truthfully.

const appDir = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

const telemetryPageSource = read(
  appDir, '(product)', 'monitoring-sources', '[targetId]', 'telemetry', 'page.tsx',
);
const streamClientSource = read(appDir, 'telemetry-stream-client.ts');
const proxyRouteSource = read(appDir, 'api', 'stream', 'telemetry', 'route.ts');

// --- SSE client transport -------------------------------------------------------

test('telemetry stream client posts to the telemetry SSE proxy with custom headers', () => {
  // Native EventSource cannot send Authorization + X-Workspace-Id, so the client
  // must use fetch() + ReadableStream against the /api/stream/telemetry proxy.
  expect(streamClientSource).toContain("const SSE_PROXY_PATH = '/api/stream/telemetry'");
  expect(streamClientSource).toContain('export function connectTelemetryStream');
  expect(streamClientSource).toContain('response.body.getReader()');
  expect(streamClientSource).toContain('Accept: \'text/event-stream\'');
});

test('telemetry stream client reconnects with backoff and resumes with Last-Event-ID', () => {
  expect(streamClientSource).toContain("onStatusChange('reconnecting')");
  expect(streamClientSource).toContain('RECONNECT_DELAY_MS');
  expect(streamClientSource).toContain("requestHeaders['Last-Event-ID'] = lastEventId");
});

test('telemetry stream client uses EXPONENTIAL backoff, reset on connect', () => {
  // Delay grows across consecutive failures and is capped, not a fixed interval.
  expect(streamClientSource).toContain('MAX_RECONNECT_DELAY_MS');
  expect(streamClientSource).toContain('export function reconnectDelayMs');
  expect(streamClientSource).toContain('Math.min(exp, MAX_RECONNECT_DELAY_MS)');
  expect(streamClientSource).toContain('reconnectDelayMs(reconnectAttempt)');
  // A successful connect resets the attempt counter so one blip never pins the
  // next retry at the ceiling.
  expect(streamClientSource).toContain('reconnectAttempt = 0;');
});

// --- Proxy route ----------------------------------------------------------------

test('telemetry SSE proxy route forwards auth + workspace and disables caching', () => {
  expect(proxyRouteSource).toContain("export const dynamic = 'force-dynamic'");
  expect(proxyRouteSource).toContain('export const revalidate = 0');
  expect(proxyRouteSource).toContain('/stream/telemetry');
  expect(proxyRouteSource).toContain("'authorization'");
  expect(proxyRouteSource).toContain("'x-workspace-id'");
  expect(proxyRouteSource).toContain("'last-event-id'");
  expect(proxyRouteSource).toContain("'Content-Type': 'text/event-stream'");
});

test('telemetry SSE proxy disables proxy buffering/compression on the stream', () => {
  // no-transform stops Railway/Next.js edge from compressing/coalescing the stream;
  // X-Accel-Buffering + keep-alive keep the socket open and unbuffered.
  expect(proxyRouteSource).toContain("'Cache-Control': 'no-cache, no-transform'");
  expect(proxyRouteSource).toContain("Connection: 'keep-alive'");
  expect(proxyRouteSource).toContain("'X-Accel-Buffering': 'no'");
});

// --- Page subscribes and prepends ----------------------------------------------

test('telemetry page subscribes to the telemetry stream on mount', () => {
  expect(telemetryPageSource).toContain('connectTelemetryStream');
  // Subscription keyed only on targetId (+ auth) so a search/filter/page change never
  // tears the stream down.
  const subEffect = telemetryPageSource.slice(telemetryPageSource.indexOf('connectTelemetryStream('));
  expect(subEffect).toContain('normalizeLiveTelemetry');
});

test('incoming telemetry event is prepended newest-first', () => {
  // Live rows accumulate newest-first and merge onto page 0 ahead of fetched rows.
  expect(telemetryPageSource).toContain('[row, ...prev]');
  expect(telemetryPageSource).toContain('const mergedRows =');
  expect(telemetryPageSource).toContain('[...matching, ...rows]');
});

test('duplicate live events are ignored (dedupe by id / tx key)', () => {
  expect(telemetryPageSource).toContain('function liveRowKey');
  expect(telemetryPageSource).toContain('r.id === row.id || liveRowKey(r) === key');
  // Merge also dedupes against fetched rows so the row never doubles once refetched.
  expect(telemetryPageSource).toContain('if (seen.has(idKey) || seen.has(txKey)) continue;');
});

test('events for another target or non-telemetry envelopes are ignored', () => {
  const normalizeSection = telemetryPageSource.slice(
    telemetryPageSource.indexOf('function normalizeLiveTelemetry'),
  );
  expect(normalizeSection).toContain("if (p.type !== 'telemetry') return null;");
  expect(normalizeSection).toContain('String(p.target_id ?? \'\') !== targetId');
  // A new target clears the live buffer so events never leak across targets.
  expect(telemetryPageSource).toContain('setLiveRows([]);');
});

// --- Active search / filter behavior --------------------------------------------

test('a live event is only injected when it matches the active search/filter', () => {
  expect(telemetryPageSource).toContain('liveRowMatchesView(r, debouncedQuery, quickFilter)');
  expect(telemetryPageSource).toContain('function liveRowMatchesQuery');
  expect(telemetryPageSource).toContain('function liveRowMatchesQuickFilter');
});

test('clearing the search re-includes already-cached live rows', () => {
  // liveRows is only reset on a target change (not on search change), and mergedRows
  // recomputes when debouncedQuery changes, so clearing the search surfaces a live
  // row that was hidden by the search.
  expect(telemetryPageSource).toContain('[rows, liveRows, currentPage, debouncedQuery, quickFilter]');
  const resetEffect = telemetryPageSource.slice(telemetryPageSource.indexOf('setLiveRows([]);'));
  expect(resetEffect).toContain('}, [targetId]);');
});

// --- Reconnect recovery + status ------------------------------------------------

test('reconnect triggers a single silent recovery refetch', () => {
  expect(telemetryPageSource).toContain('fetchTelemetryRef.current({ silent: true })');
  expect(telemetryPageSource).toContain('sawDisconnect = true');
});

test('the page keeps a periodic HTTP fetch as the fallback and never re-sorts', () => {
  // The list fetch remains a no-store request; live merge never sorts the list.
  const fetchSection = telemetryPageSource.slice(telemetryPageSource.indexOf('const fetchTelemetry ='));
  expect(fetchSection).toContain("cache: 'no-store'");
  const rowSection = telemetryPageSource.slice(telemetryPageSource.indexOf('filteredRows.map'));
  expect(rowSection).not.toContain('.sort(');
});

test('HTTP refresh fallback runs ONLY while the SSE stream is not live', () => {
  // The label "HTTP refresh fallback active" must be truthful: a real periodic
  // refetch runs while disconnected (surfacing a row persisted during an SSE
  // outage — the production symptom) and is cleared once the stream is live so a
  // healthy connection does not aggressively poll.
  expect(telemetryPageSource).toContain('HTTP_FALLBACK_POLL_MS');
  const fallbackEffect = telemetryPageSource.slice(
    telemetryPageSource.indexOf('Periodic HTTP refresh fallback'),
  );
  expect(fallbackEffect).toContain("if (streamStatus === 'live') return;");
  expect(fallbackEffect).toContain('setInterval(');
  expect(fallbackEffect).toContain('fetchTelemetryRef.current({ silent: true })');
  expect(fallbackEffect).toContain('clearInterval(timer)');
  expect(fallbackEffect).toContain('}, [targetId, streamStatus]);');
});

test('real-time status is truthful and does not claim paused while connected', () => {
  expect(telemetryPageSource).toContain('data-testid="telemetry-stream-status"');
  expect(telemetryPageSource).toContain('Live — streaming new events');
  expect(telemetryPageSource).toContain('const telemetryStreamConnected = streamStatus === \'live\';');
  // Requirement 7: the legacy "Realtime paused" note is suppressed while the
  // telemetry SSE is connected.
  expect(telemetryPageSource).toContain('telemetryStreamConnected ? null :');
});

test('QuickNode status distinguishes live / catching up / degraded / stale / failed', () => {
  expect(telemetryPageSource).toContain("case 'live':");
  expect(telemetryPageSource).toContain('Live at chain tip');
  expect(telemetryPageSource).toContain('Catching up (historical backfill)');
  expect(telemetryPageSource).toContain('blocks behind (stable polling fallback)');
  expect(telemetryPageSource).toContain('Stale — live lane not advancing');
  expect(telemetryPageSource).toContain('quicknode_live_lane_state');
});

// --- Row count + empty states ---------------------------------------------------

test('row count includes live rows injected before the next refetch', () => {
  expect(telemetryPageSource).toContain('const injectedLiveCount =');
  expect(telemetryPageSource).toContain('const displayTotalCount = totalCount + injectedLiveCount;');
  expect(telemetryPageSource).toContain('of {displayTotalCount} row');
});

test('empty search state says no match, not that the target has no telemetry', () => {
  expect(telemetryPageSource).toContain('No telemetry matches this search');
  // The "no data at all" copy only shows without an active search/filter.
  expect(telemetryPageSource).toContain("filteredRows.length === 0 && (debouncedQuery.trim() !== '' || quickFilter !== 'all')");
});

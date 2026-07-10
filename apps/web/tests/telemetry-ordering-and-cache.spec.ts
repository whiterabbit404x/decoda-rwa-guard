import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Frontend contract for the telemetry ordering + freshness fix:
//   * The list is rendered in the exact order the backend returns it (newest-first is
//     a DETERMINISTIC BACKEND order — the page must not re-sort or re-tier client-side).
//   * Every telemetry request is a fresh no-store fetch, so a newly persisted event is
//     visible after the normal refresh cycle without a full browser reload, and
//     clearing the search restores a freshly fetched newest-first default list.

const appDir = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

const telemetryPageSource = read(
  appDir, '(product)', 'monitoring-sources', '[targetId]', 'telemetry', 'page.tsx',
);
const proxyRouteSource = read(
  appDir, 'api', 'monitoring', 'targets', '[targetId]', 'telemetry', 'route.ts',
);
const monitoringRunnerPy = read(
  __dirname, '..', '..', '..', 'services', 'api', 'app', 'monitoring_runner.py',
);

// --- Backend deterministic order is authoritative -------------------------------

test('backend applies a deterministic newest-first order with stable tie-breakers', () => {
  expect(monitoringRunnerPy).toContain('te.observed_at DESC NULLS LAST');
  expect(monitoringRunnerPy).toContain('te.ingested_at DESC NULLS LAST');
  expect(monitoringRunnerPy).toContain('te.id DESC');
  // The old event_type tiering that pushed newer native_transfer rows down is gone.
  expect(monitoringRunnerPy).not.toContain(
    "CASE WHEN te.event_type = 'wallet_transfer_detected' THEN 0 ELSE 1 END",
  );
});

// --- Frontend does not re-order the backend result ------------------------------

test('telemetry page renders rows in backend order (no client-side sort)', () => {
  // The page maps filteredRows straight into <tr> rows; it must never call .sort() on
  // the telemetry list, so the backend newest-first order is what the user sees.
  const rowSection = telemetryPageSource.slice(telemetryPageSource.indexOf('filteredRows.map'));
  expect(rowSection).not.toContain('.sort(');
  // For the default "All" filter, filteredRows is exactly the fetched rows.
  expect(telemetryPageSource).toContain('const filteredRows =');
  expect(telemetryPageSource).toContain(': rows;');
});

// --- Fresh, uncached fetch (no stale default list) ------------------------------

test('telemetry list fetch is no-store so new events appear without a full reload', () => {
  const fetchSection = telemetryPageSource.slice(
    telemetryPageSource.indexOf('buildTelemetryUrl(targetId'),
  );
  expect(fetchSection).toContain("cache: 'no-store'");
});

test('clearing the search re-fetches the default list (debouncedQuery is an effect dep)', () => {
  // The load effect re-runs whenever debouncedQuery changes, so emptying the search box
  // triggers a fresh default (newest-first) request rather than reusing cached rows.
  expect(telemetryPageSource).toContain('[targetId, debouncedQuery, quickFilter, currentPage, authHeaders]');
});

test('proxy route disables caching end-to-end (force-dynamic + no-store)', () => {
  expect(proxyRouteSource).toContain("export const dynamic = 'force-dynamic'");
  expect(proxyRouteSource).toContain('export const revalidate = 0');
  expect(proxyRouteSource).toContain("'Cache-Control': 'no-store'");
  expect(proxyRouteSource).toContain("cache: 'no-store'");
});

// --- Loading / empty / error states remain intact -------------------------------

test('telemetry page keeps loading, empty, and error states', () => {
  expect(telemetryPageSource).toContain('Loading telemetry...');
  expect(telemetryPageSource).toContain('No telemetry data');
  expect(telemetryPageSource).toContain('Unable to load telemetry');
  expect(telemetryPageSource).toContain('rows.length === 0');
});

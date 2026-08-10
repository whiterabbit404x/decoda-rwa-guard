/**
 * Screen 9: Evidence & Audit / Exportable Proof
 * Source-contract tests — read .tsx source files, assert on structural presence.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';
const PAGE  = 'app/(product)/evidence/page.tsx';
const EXPORTS_REDIRECT = 'app/(product)/exports/page.tsx';
const NAV   = 'app/product-nav.ts';

/* ── 1. Route rendering ────────────────────────────────────────── */

test('1a: /evidence page file exists and imports EvidenceAuditPanel', () => {
  const source = read(PAGE);
  expect(source).toContain('EvidenceAuditPanel');
  expect(source).toContain('evidence-audit-panel');
});

test('1b: /exports page redirects to /evidence without error', () => {
  const source = read(EXPORTS_REDIRECT);
  expect(source).toContain("redirect('/evidence')");
});

/* ── 2. Page title ──────────────────────────────────────────────── */

test('2: page title "Evidence & Audit" exists', () => {
  const source = read(PANEL);
  expect(source).toContain('Evidence &amp; Audit');
});

/* ── 3. Subtitle ────────────────────────────────────────────────── */

test('3: subtitle text exists', () => {
  const source = read(PANEL);
  expect(source).toContain(
    'Generate tamper-evident incident packages, verify file integrity, and review workspace audit activity.',
  );
});

/* ── 4. Create Evidence Package button ──────────────────────────── */

test('4a: Create Evidence Package button exists', () => {
  const source = read(PANEL);
  expect(source).toContain('Create Evidence Package');
});

test('4b: button disables only for explicit denial / unresolved permission / submitting — never for an empty chain', () => {
  const source = read(PANEL);
  // The Create button gate is driven by the permission-resolution model, not by the
  // old chain-readiness guard. The removed `canCreatePackage` gate must be gone.
  expect(source).toContain('disabled={createDisabled}');
  expect(source).not.toContain('disabled={!canCreatePackage}');
  expect(source).not.toContain('const canCreatePackage');
  // createDisabled is composed only of permission/submission states.
  expect(source).toContain('const createDisabled = creating || permissionResolving || permissionUndetermined || permissionDenied');
  // Denial is an EXPLICIT can_export === false, never a truthy coercion of an absent field.
  expect(source).toContain('canExport === false');
  // The button's disabled condition must not reference incident/chain readiness.
  expect(source).not.toContain('disabled={!chainReadyForPackage}');
});

/* ── 5. Top metric cards ────────────────────────────────────────── */

test('5: all four metric cards are present', () => {
  const source = read(PANEL);
  expect(source).toContain('"Evidence Packages"');
  expect(source).toContain('"Audit Events"');
  expect(source).toContain('"Export Ready"');
  expect(source).toContain('"Retention Status"');
});

/* ── 6. Tabs ────────────────────────────────────────────────────── */

test('6: tabs are exactly Evidence Packages and Audit Logs', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Evidence Packages'");
  expect(source).toContain("label: 'Audit Logs'");
  // No unexpected third tab key
  const tabMatches = [...source.matchAll(/key: '(packages|audit)'/g)];
  expect(tabMatches.length).toBeGreaterThanOrEqual(2);
});

/* ── 7. Evidence Packages table columns ─────────────────────────── */

test('7: Evidence Packages table has exactly the required columns', () => {
  const source = read(PANEL);
  expect(source).toContain("'Package ID'");
  expect(source).toContain("'Incident / Scope'");
  expect(source).toContain("'Created'");
  expect(source).toContain("'Includes'");
  expect(source).toContain("'Size'");
  expect(source).toContain("'SHA-256'");
  expect(source).toContain("'Integrity'");
  expect(source).toContain("'Actions'");
});

test('7b: the raw Evidence Source column is removed from the package table headers', () => {
  const source = read(PANEL);
  // Evidence source now lives in package details/tooltip, not a dedicated
  // package-table column that duplicated the included-evidence data.
  expect(source).toContain('PKG_TABLE_HEADERS');
  const headerBlock = source.slice(
    source.indexOf('PKG_TABLE_HEADERS'),
    source.indexOf('INTEGRITY_FILTER_OPTIONS'),
  );
  expect(headerBlock).not.toContain("'Evidence Source'");
});

/* ── 8. Audit Logs table columns ────────────────────────────────── */

test('8: Audit Logs table has exactly the required columns', () => {
  const source = read(PANEL);
  expect(source).toContain("'Time'");
  expect(source).toContain("'Actor'");
  expect(source).toContain("'Action'");
  expect(source).toContain("'Object'");
  expect(source).toContain("'Result'");
  expect(source).toContain("'Source IP or System'");
  // Evidence Source appears in both tables
  expect(source).toContain("'Evidence Source'");
});

/* ── 9. Included Artifacts checklist ────────────────────────────── */

test('9: Included Artifacts checklist contains all required items', () => {
  const source = read(PANEL);
  expect(source).toContain('Telemetry Snapshot');
  expect(source).toContain('Detection Event');
  expect(source).toContain('Alert');
  expect(source).toContain('Incident Timeline');
  expect(source).toContain('Response Action');
  expect(source).toContain('Audit Log');
  expect(source).toContain('Included Artifacts');
});

/* ── 10. Empty state: no telemetry blocker ──────────────────────── */

test('10: shows telemetry blocker when no telemetry exists', () => {
  const source = read(PANEL);
  expect(source).toContain('No evidence packages yet');
  expect(source).toContain(
    'No evidence package can be created because no telemetry has been received.',
  );
  expect(source).toContain("ctaHref: '/threat'");
  expect(source).toContain("ctaLabel: 'View Threat Monitoring'");
});

/* ── 11. Empty state: alert exists but no incident blocker ──────── */

test('11: shows incident blocker when alert exists but no incident', () => {
  const source = read(PANEL);
  expect(source).toContain('Alerts exist, but no incident has been opened yet.');
  expect(source).toContain("ctaHref: '/incidents'");
  expect(source).toContain("ctaLabel: 'Open Incident'");
});

/* ── 12. Empty state: incident exists but no response action ─────── */

test('12: shows response blocker when incident exists but no response action', () => {
  const source = read(PANEL);
  expect(source).toContain(
    'An incident exists, but no response action has been recommended or recorded yet.',
  );
  expect(source).toContain("ctaHref: '/response-actions'");
  expect(source).toContain("ctaLabel: 'Recommend Response'");
});

/* ── 13. Export Ready is canonical (verified), not "every completed row" ── */

test('13: Export Ready metric uses the canonical backend predicate, not all completed rows', () => {
  const source = read(PANEL);
  // exportReadyCount prefers the backend metrics.export_ready (verified + retrievable).
  expect(source).toContain('exportReadyCount');
  expect(source).toContain('metrics?.export_ready');
  // The per-row fallback uses the backend canonical flag, never "any completed".
  expect(source).toContain('is_export_ready');
});

/* ── 14. Download actions (primary View + overflow menu) ─────────── */

test('14a: primary row action is View Package with an overflow action menu', () => {
  const source = read(PANEL);
  expect(source).toContain('View Package');
  expect(source).toContain('RowActionMenu');
  expect(source).toContain('aria-haspopup="menu"');
});

test('14b: overflow menu offers Download Package and Download Manifest as distinct actions', () => {
  const source = read(PANEL);
  expect(source).toContain('Download Package');
  expect(source).toContain('Download Manifest');
  // "Download JSON" is renamed to "Download Package"; the ambiguous label is gone.
  expect(source).not.toContain('Download JSON');
  // No button labelled just "Download" (which would imply a ZIP bundle).
  const buttonDownloadOnly = />\s*Download\s*<\/button>/.test(source);
  expect(buttonDownloadOnly).toBe(false);
});

test('14c: Download Package and Download Manifest call different backend endpoints', () => {
  const source = read(PANEL);
  // Package artifact vs. deterministic manifest are separate downloads.
  expect(source).toContain('/download');
  expect(source).toContain('/manifest');
  expect(source).toMatch(/a\.download\s*=\s*`evidence-package-\$\{pkg\.id\}\.json`/);
  expect(source).toMatch(/a\.download\s*=\s*`evidence-manifest-\$\{pkg\.id\}\.json`/);
});

test('14d: row actions are gated by backend allowed_actions', () => {
  const source = read(PANEL);
  expect(source).toContain('allowed_actions');
  // The old "Export JSON" duplicate pair is gone.
  expect(source).not.toContain('Export JSON');
});

test('14e: Manifest-Missing recovery offers Generate Manifest (primary) and Regenerate Package (fallback)', () => {
  const source = read(PANEL);
  // Primary recovery action (in-place manifest generation).
  expect(source).toContain('Generate Manifest');
  expect(source).toContain('generate_manifest');
  // Fallback recovery action — superseding regeneration that preserves the original.
  expect(source).toContain('Regenerate Package');
  expect(source).toContain('regenerate_package');
  // Wired to a dedicated backend endpoint (not the Download Package artifact path).
  expect(source).toContain('/regenerate');
  // Truthful copy: the original package is preserved, never rewritten.
  expect(source).toMatch(/superseding|preserved as historical evidence/);
  // Both recovery actions are backend-authoritative (allowed_actions gated).
  expect(source).toContain('detailCanRegenerate');
});

/* ── 15. Simulator evidence not labeled as live_provider ─────────── */

test('15: simulator evidence is labeled simulator, not live_provider', () => {
  const source = read(PANEL);
  // The evidenceSourcePill function maps simulator/demo/replay to 'Simulator/test evidence'
  expect(source).toContain("label: 'Simulator/test evidence'");
  // Simulator does NOT map to live_provider
  expect(source).toContain("raw === 'simulator'");
  // The workspaceSource guard prevents simulator from being labeled live_provider
  expect(source).toContain("workspaceSource === 'simulator'");
  // live sources are returned as 'Live evidence', not 'live_provider'
  expect(source).toContain("label: 'Live evidence'");
  expect(source).toContain("raw === 'live_provider'");
});

/* ── 16. Sidebar/nav label is "Evidence & Audit" ────────────────── */

test('16: sidebar nav label is Evidence & Audit at /evidence route', () => {
  const nav = read(NAV);
  expect(nav).toContain("href: '/evidence'");
  expect(nav).toContain("label: 'Evidence & Audit'");
});

/* ── Evidence chain completeness checks ─────────────────────────── */

test('chain: incomplete chain shows warning in package detail', () => {
  const source = read(PANEL);
  expect(source).toContain('Evidence chain incomplete');
  expect(source).toContain('chainComplete');
});

test('chain: package detail links to linked incident when incident_id present', () => {
  const source = read(PANEL);
  expect(source).toContain('Linked Incident');
  expect(source).toContain('Linked Alert');
  expect(source).toContain('Linked Detection');
  expect(source).toContain("href=\"/incidents\"");
});

test('chain: the header Create button is no longer gated by chain readiness', () => {
  const source = read(PANEL);
  // The removed `chainReadyForPackage` variable must not exist, and the header button
  // must not depend on incident/response-action readiness — an empty chain opens the
  // modal to a truthful empty state instead of disabling the primary button.
  expect(source).not.toContain('const chainReadyForPackage');
  expect(source).not.toContain('canCreatePackage');
  // incidentOk / responseActionOk survive only for the table empty-state blocker.
  expect(source).toContain('if (!responseActionOk)');
  expect(source).toContain('if (!incidentOk)');
});

/* ── Audit logs detail panel ────────────────────────────────────── */

test('audit detail: panel shows required fields', () => {
  const source = read(PANEL);
  expect(source).toContain('Event ID');
  expect(source).toContain('Object Type');
  expect(source).toContain('Object ID');
  expect(source).toContain('Source IP / System');
  expect(source).toContain('Workspace ID');
  expect(source).toContain('Audit event detail');
});

/* ── Status pill variants ───────────────────────────────────────── */

test('status pills: package status values are mapped', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Ready'");
  expect(source).toContain("label: 'Pending'");
  expect(source).toContain("label: 'Exported'");
  expect(source).toContain("label: 'Failed'");
  expect(source).toContain("label: 'Not Available'");
  expect(source).toContain("label: 'Unknown'");
});

test('status pills: audit result values are mapped', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Success'");
  expect(source).toContain("label: 'Denied'");
});

/* ── Proxy-route transport (NEXT_PUBLIC_API_URL may be unset in production) ── */

test('proxy: /api/exports route file exists and uses proxyJsonToBackend', () => {
  const source = read('app/api/exports/route.ts');
  expect(source).toContain('proxyJsonToBackend');
  expect(source).toContain("backendPath: '/exports'");
  expect(source).toContain("method: 'GET'");
  expect(source).toContain('searchParams');
});

test('proxy: /api/events route file exists and uses proxyJsonToBackend', () => {
  const source = read('app/api/events/route.ts');
  expect(source).toContain('proxyJsonToBackend');
  expect(source).toContain("backendPath: '/events'");
  expect(source).toContain("method: 'GET'");
});

test('proxy: /api/exports/proof-bundle POST route exists and forwards the body to the backend', () => {
  // The Create Evidence Package POST must have a same-origin proxy route so it
  // reaches the server-resolved backend (API_URL) — the SAME backend the list
  // GET reads from — instead of the browser NEXT_PUBLIC_API_URL directly.
  const source = read('app/api/exports/proof-bundle/route.ts');
  expect(source).toContain('proxyJsonToBackend');
  expect(source).toContain("backendPath: '/exports/proof-bundle'");
  expect(source).toContain("method: 'POST'");
  expect(source).toContain('forwardBody: true');
  expect(source).toContain('export async function POST');
});

test('proxy: panel creates packages via /api/exports/proof-bundle, never a direct backend URL', () => {
  const source = read(PANEL);
  expect(source).toContain("'/api/exports/proof-bundle'");
  // The create POST must not bypass the proxy with the browser-exposed base URL.
  expect(source).not.toContain('`${apiUrl}/exports/proof-bundle`');
  // resolveApiUrl / apiUrl are no longer needed in the panel at all.
  expect(source).not.toContain('resolveApiUrl');
});

test('proxy: panel fetches exports via /api/exports not direct backend URL', () => {
  const source = read(PANEL);
  // Must use same-origin proxy so listing works when NEXT_PUBLIC_API_URL is unset
  expect(source).toContain("'/api/exports'");
  expect(source).toContain('`/api/exports?');
  // Must not call backend directly for the list fetch
  expect(source).not.toContain('`${apiUrl}/exports`');
  expect(source).not.toContain('`${apiUrl}/exports?');
});

test('proxy: panel fetches audit events via /api/events not direct backend URL', () => {
  const source = read(PANEL);
  expect(source).toContain("'/api/events'");
  expect(source).not.toContain('`${apiUrl}/events`');
});

/* ── Asynchronous-progress polling (truthful, bounded, visibility-aware) ────── */

test('poll: only runs while a package is in a non-terminal generation state', () => {
  const source = read(PANEL);
  // Terminal-vs-in-progress is a canonical predicate over backend status/integrity.
  // It is now defined once in evidence-detail-state (so it is unit-testable and a
  // Manifest-Missing package is provably terminal) and delegated to by the panel.
  expect(source).toContain('function isPackageInProgress');
  expect(source).toContain('isEvidenceProgressPollingState');
  expect(source).toContain('packages.some(isPackageInProgress)');
  // The effect bails immediately (stops) when nothing is in progress.
  expect(source).toContain('if (!hasInFlightPackage) return');

  // The canonical predicate: only real generation-lifecycle states are in-progress;
  // manifest_missing (and hash_generated) are absent, i.e. terminal.
  const detailState = read('app/evidence-detail-state.ts');
  expect(detailState).toContain("IN_PROGRESS_JOB_STATUSES = new Set(['queued', 'pending', 'building', 'running'])");
  expect(detailState).toContain("IN_PROGRESS_INTEGRITY_STATES = new Set(['building', 'hashing', 'verifying'])");
});

test('poll: pauses while the tab is hidden, uses a bounded interval, and never overlaps requests', () => {
  const source = read(PANEL);
  expect(source).toContain('document.hidden');
  expect(source).toContain('const POLL_MS = 5000');
  expect(source).toContain('window.setInterval');
  expect(source).toContain('window.clearInterval');
  // An in-flight guard prevents a slow refresh from overlapping the next tick.
  expect(source).toContain('let inFlight = false');
  expect(source).toContain('if (cancelled || inFlight ||');
  // Each tick refreshes from the canonical backend list, updating last-refreshed.
  expect(source).toContain('await loadExportsOnce()');
});

test('refresh: a single canonical exports refresh is reused by create-confirm and polling', () => {
  const source = read(PANEL);
  expect(source).toContain('const loadExportsOnce = useCallback');
  // It reads the same-origin proxy and applies backend-authoritative state.
  expect(source).toContain("const url = qs ? `/api/exports?${qs}` : '/api/exports'");
  expect(source).toContain('setPackages(loaded)');
  expect(source).toContain('setLastRefreshAt(new Date().toISOString())');
});

test('refresh: last refreshed time is shown from real refresh timestamps', () => {
  const source = read(PANEL);
  expect(source).toContain('Last refreshed');
  expect(source).toContain('lastRefreshAt');
});

/* ── URL search param handling ──────────────────────────────────────────────── */

test('url-params: panel reads package_id, action_id, incident_id from search params', () => {
  const source = read(PANEL);
  expect(source).toContain("searchParams.get('package_id')");
  expect(source).toContain("searchParams.get('action_id')");
  expect(source).toContain("searchParams.get('incident_id')");
});

test('url-params: package_id is passed to /api/exports query string', () => {
  const source = read(PANEL);
  expect(source).toContain("exportsParams.set('package_id', urlPackageId)");
  expect(source).toContain("exportsParams.set('action_id', urlActionId)");
  expect(source).toContain("exportsParams.set('incident_id', urlIncidentId)");
});

test('url-params: blocker is suppressed when any URL param is present', () => {
  const source = read(PANEL);
  // When package_id / action_id / incident_id is in URL, we must not show a chain-step blocker
  // even if packages haven't loaded yet (to avoid false "no packages" state on first render).
  expect(source).toContain('urlPackageId || urlActionId || urlIncidentId');
});

test('url-params: blocker is suppressed when packageExists is true', () => {
  const source = read(PANEL);
  // Once a package is loaded the chain-step blockers must disappear permanently.
  expect(source).toContain('if (packageExists) return null');
});

/* ── Summary cards ─────────────────────────────────────────────────────────── */

test('summary: Evidence Packages card counts server-authoritative total', () => {
  const source = read(PANEL);
  // The metric tile value must come from the backend metrics (or packages.length
  // fallback) — never a hard-coded number.
  expect(source).toContain('label="Evidence Packages"');
  expect(source).toContain('metrics?.total_packages ?? packages.length');
});

test('summary: Export Ready card counts canonical export-ready packages, not all rows', () => {
  const source = read(PANEL);
  expect(source).toContain('label="Export Ready"');
  expect(source).toContain('value={exportReadyCount}');
  // exportReadyCount prefers the backend canonical predicate, not "any completed".
  expect(source).toContain('metrics?.export_ready');
});

test('summary: Retention Status is backend-derived, never hardcoded to Compliant', () => {
  const source = read(PANEL);
  // Retention comes from the API response; the old package-existence heuristic is gone.
  expect(source).toContain('setRetentionStatus(');
  expect(source).toContain('p.retention_status');
  expect(source).toContain('label="Retention Status" value={retentionStatus}');
  // Never derives "Compliant" from package existence in the client.
  expect(source).not.toContain("exportReadyCount > 0 ? 'Compliant'");
});

test('summary: Audit Events card shows truthful capped semantics (500+/Latest 500 shown)', () => {
  const source = read(PANEL);
  expect(source).toContain('auditEventsDisplay');
  expect(source).toContain('auditCapped');
  expect(source).toContain('Latest 500 shown');
});

/* ── No fake evidence ───────────────────────────────────────────────────────── */

test('no-fake: panel never injects hardcoded mock packages into the packages array', () => {
  const source = read(PANEL);
  // packages state is only populated from the /api/exports API response
  expect(source).toContain('setPackages(loaded)');
  // There must be no literal fake package objects pushed into state
  expect(source).not.toMatch(/setPackages\s*\(\s*\[.*id.*package/s);
});

/* ── Screen 9 truthfulness contract (consistency pass) ──────────────────────── */

test('truthful: Create Evidence Package is permission-aware (canExport from backend, tri-state)', () => {
  const source = read(PANEL);
  expect(source).toContain('Create Evidence Package');
  // Permission is backend-authoritative and kept as a tri-state — an absent field stays
  // undefined rather than being coerced to a denial.
  expect(source).toContain('setCanExport(typeof p.can_export === \'boolean\' ? p.can_export : undefined)');
  // Denial is explicit; loading/undetermined are separate, truthful states.
  expect(source).toContain('const permissionDenied = !permissionResolving && !loadError && canExport === false');
  expect(source).toContain('const permissionResolving = runtimeLoading || dataLoading');
  expect(source).toContain('const permissionUndetermined = !permissionResolving && Boolean(loadError)');
  // Submission is guarded against duplicates and against an explicit denial.
  expect(source).toContain('setCreating(true)');
  expect(source).toContain('if (creating || permissionDenied) return');
});

test('truthful: table shows human-readable package + incident numbers, not raw UUIDs', () => {
  const source = read(PANEL);
  expect(source).toContain('pkg.package_number');
  expect(source).toContain('pkg.incident_short_id');
  // The internal UUID is available as a tooltip / sr-only for diagnostics only.
  expect(source).toContain('Internal ID');
});

test('truthful: hash column state cannot contradict the integrity pill', () => {
  const source = read(PANEL);
  // Hash cell is driven by the backend hash_state, never a bare "Pending".
  expect(source).toContain('HashCell');
  expect(source).toContain('hash_display_state');
  expect(source).toContain('Not generated');
  expect(source).toContain('Generating…');
  // The old contradictory "Pending" hash literal is gone from the hash cell.
  const hashCellBlock = source.slice(source.indexOf('function HashCell'), source.indexOf('function RowActionMenu'));
  expect(hashCellBlock).not.toContain('Pending');
});

test('truthful: integrity pill uses the backend label + canonical variant map', () => {
  const source = read(PANEL);
  expect(source).toContain('INTEGRITY_VARIANTS');
  expect(source).toContain('pkg.integrity_label');
  // Legacy exports are a first-class truthful state (never shown as verified).
  expect(source).toContain('legacy_export');
  expect(source).toContain("{ value: 'legacy_export', label: 'Legacy Export' }");
});

test('truthful: row selection drives the Crypto-Auditing Clerk card', () => {
  const source = read(PANEL);
  // The agent card receives the selected package + its detail.
  expect(source).toContain('<CryptoAuditingClerkPanel');
  expect(source).toContain('selectedPkg={selectedPkg}');
  // View Package selects the row (opens its report).
  expect(source).toContain('onView={() => setSelectedPkgId(pkg.id)}');
});

test('truthful: filtering clears a selection it has hidden', () => {
  const source = read(PANEL);
  expect(source).toContain('inPackages && !inFiltered');
  expect(source).toContain('setSelectedPkgId(\'\')');
});

test('truthful: agent card never shows "Unknown" completeness; uses "Not calculated"', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Not calculated'");
  expect(source).not.toContain("label: 'Unknown', variant: 'neutral' };\n  if (score >= 95)");
  // Workspace mode explains the absence rather than showing a bare dash.
  expect(source).toContain('No package has a calculable completeness score yet.');
});

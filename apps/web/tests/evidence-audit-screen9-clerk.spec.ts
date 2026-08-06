/**
 * Screen 9 — Evidence & Audit: Crypto-Auditing Clerk, integrity verification,
 * SHA-256 hash display, manifest download, completeness scoring, and filters.
 *
 * Source-contract tests (read the .tsx / route source and assert structural
 * presence), matching the existing evidence-audit-screen9.spec.ts pattern.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';

/* ── Crypto-Auditing Clerk sidebar ──────────────────────────────── */

test('clerk: agent sidebar renders with title and primary report action', () => {
  const source = read(PANEL);
  expect(source).toContain('Crypto-Auditing Clerk');
  expect(source).toContain('CryptoAuditingClerkPanel');
  expect(source).toContain('View Full Package Report');
  // The label is corrected from the misleading "AI Evidence Completeness".
  expect(source).toContain('Evidence Completeness');
  expect(source).not.toContain('AI Evidence Completeness');
});

test('clerk: package mode shows real per-package evidence counts', () => {
  const source = read(PANEL);
  // Package-scoped metrics come from the backend completeness + verification result.
  expect(source).toContain('completeness.present_count');
  expect(source).toContain('completeness.missing_count');
  expect(source).toContain('completeness.unverifiable_count');
  expect(source).toContain('detail?.verification?.files_verified');
});

test('clerk: evidence metrics section lists the required tiles', () => {
  const source = read(PANEL);
  expect(source).toContain('Evidence Metrics');
  expect(source).toContain('Total Packages');
  expect(source).toContain('Total Size');
  expect(source).toContain('Incidents Covered');
  expect(source).toContain('Files Hashed');
  expect(source).toContain('Integrity Failures');
});

test('clerk: completeness ring + status bands (Excellent/Good/Incomplete/Critical)', () => {
  const source = read(PANEL);
  expect(source).toContain('CompletenessRing');
  expect(source).toContain("label: 'Excellent'");
  expect(source).toContain("label: 'Good'");
  expect(source).toContain("label: 'Incomplete'");
  expect(source).toContain("label: 'Critical'");
});

test('clerk: completeness score is derived from backend values, never hardcoded', () => {
  const source = read(PANEL);
  // Must read the backend-provided score, not a literal.
  expect(source).toContain('completeness_score');
  expect(source).toContain('average_completeness');
  expect(source).toContain('completeness?.score');
  // The infamous demo value must not be hardcoded anywhere.
  expect(source).not.toContain('97%');
  expect(source).not.toContain('97% Excellent');
});

test('clerk: verification checklist is computed from real package data', () => {
  const source = read(PANEL);
  expect(source).toContain('Verification Checklist');
  expect(source).toContain('completeness?.checklist');
});

/* ── Hash (SHA-256) column ──────────────────────────────────────── */

test('hash: table has a SHA-256 column with truncation and copy', () => {
  const source = read(PANEL);
  expect(source).toContain("'SHA-256'");
  expect(source).toContain('truncHash');
  expect(source).toContain('copyHash');
  // Full hash remains available to screen readers and in a title tooltip.
  expect(source).toContain('Full SHA-256 hash');
});

test('hash: truncated value is not stored as the real hash (full value preserved)', () => {
  const source = read(PANEL);
  // The copy action copies the full integrity_hash, not the truncated display.
  expect(source).toContain('packageHash(pkg)');
  expect(source).toContain('copyToClipboard');
});

/* ── Integrity status column + states ───────────────────────────── */

test('integrity: table has an Integrity column with backend-driven pill', () => {
  const source = read(PANEL);
  expect(source).toContain("'Integrity'");
  expect(source).toContain('integrityPill');
  // Integrity is read from the backend, never computed in the browser.
  expect(source).toContain('pkg.integrity_status');
});

test('integrity: verified and integrity-failed states are distinctly mapped', () => {
  const source = read(PANEL);
  // Labels are backend-authoritative (integrity_label); the client maps status to a
  // colour variant. "Hash Generated" is a distinct state from "Verified", and a
  // legacy/unhashed export is never either.
  expect(source).toContain('INTEGRITY_VARIANTS');
  expect(source).toContain('verified:');
  expect(source).toContain('hash_generated:');
  expect(source).toContain('integrity_failed:');
  expect(source).toContain('needs_evidence:');
  expect(source).toContain('superseded:');
  expect(source).toContain('legacy_export:');
  expect(source).toContain('pkg.integrity_label');
});

test('integrity: integrity-failed package shows a high-visibility warning, not a verified download', () => {
  const source = read(PANEL);
  expect(source).toContain('Integrity Failed');
  expect(source).toContain('changed after generation');
  expect(source).toContain("role=\"alert\"");
});

/* ── Verify + Manifest actions ──────────────────────────────────── */

test('actions: Verify Integrity and Download Manifest are present and readiness-gated', () => {
  const source = read(PANEL);
  expect(source).toContain('Verify Integrity');
  expect(source).toContain('Download Manifest');
  expect(source).toContain('verifyPackage');
  expect(source).toContain('downloadManifest');
  // Verify posts to the backend verify endpoint (authoritative there).
  expect(source).toContain('/verify');
  // Manifest downloads as a .json file.
  expect(source).toMatch(/a\.download\s*=\s*`evidence-manifest-\$\{pkg\.id\}\.json`/);
});

/* ── Search + integrity filter ──────────────────────────────────── */

test('filters: search box and integrity-status filter drive the visible rows', () => {
  const source = read(PANEL);
  expect(source).toContain('filteredPackages');
  expect(source).toContain('integrityFilter');
  expect(source).toContain('Search evidence packages');
  expect(source).toContain('INTEGRITY_FILTER_OPTIONS');
  expect(source).toContain('No packages match these filters.');
});

/* ── Loading / error states ─────────────────────────────────────── */

test('states: skeleton, error-with-retry, and empty states render', () => {
  const source = read(PANEL);
  expect(source).toContain('skeletonRow');
  expect(source).toContain('Evidence packages could not be loaded.');
  expect(source).toContain('Retry');
  expect(source).toContain('No evidence packages yet.');
});

/* ── Backend authority ──────────────────────────────────────────── */

test('authority: metrics + integrity + completeness all come from the API payload', () => {
  const source = read(PANEL);
  // metrics come from the list response, not fabricated.
  expect(source).toContain('setMetrics(p.metrics');
  // detail (files/completeness/verification) comes from GET /api/exports/{id}.
  expect(source).toContain('setSelectedDetail');
  expect(source).toContain('/api/exports/${encodeURIComponent(selectedPkgId)}');
});

/* ── Proxy routes ───────────────────────────────────────────────── */

test('proxy: verify route posts to backend /exports/{id}/verify', () => {
  const source = read('app/api/exports/[packageId]/verify/route.ts');
  expect(source).toContain('proxyJsonToBackend');
  expect(source).toContain("method: 'POST'");
  expect(source).toContain('/verify');
});

test('proxy: manifest route streams a file download with Content-Disposition', () => {
  const source = read('app/api/exports/[packageId]/manifest/route.ts');
  expect(source).toContain('/manifest');
  expect(source).toContain('Content-Disposition');
  expect(source).toContain('evidence-manifest-');
  // Never exposes the backend URL / credentials to the browser.
  expect(source).toContain('Authorization');
});

test('proxy: package detail route proxies GET /exports/{id}', () => {
  const source = read('app/api/exports/[packageId]/route.ts');
  expect(source).toContain('proxyJsonToBackend');
  expect(source).toContain("method: 'GET'");
  expect(source).toContain('/exports/${encodeURIComponent(packageId)}');
});

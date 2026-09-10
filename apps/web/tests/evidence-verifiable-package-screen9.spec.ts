/**
 * Screen 9 — Verifiable Evidence Package / Merkle Integrity.
 * Source-contract tests: read the .tsx sources and assert on structural presence.
 *
 * The rule these guard: the verification surface renders BACKEND conclusions.
 * It never computes an outcome, never defaults to a success state, and never
 * claims HSM/KMS custody the deployment does not have.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';
const VERIFICATION = 'app/evidence-package-verification.tsx';
const ARCHIVE_PROXY = 'app/api/exports/[packageId]/archive/route.ts';
const HISTORY_PROXY = 'app/api/exports/history/route.ts';

/* ── 1. Tabs ─────────────────────────────────────────────────────── */

test('1: the three canonical tabs are Evidence Packages, Audit Log, Export History', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Evidence Packages'");
  expect(source).toContain("label: 'Audit Log'");
  expect(source).toContain("label: 'Export History'");
  const keys = [...source.matchAll(/key: '(packages|audit|history)'/g)].map((m) => m[1]);
  expect(new Set(keys)).toEqual(new Set(['packages', 'audit', 'history']));
});

/* ── 2. Package summary uses real API values ─────────────────────── */

test('2a: the package summary renders backend fields, not hardcoded reference data', () => {
  const source = read(PANEL);
  expect(source).toContain('<PackageCryptoSummary');
  expect(source).toContain('merkleRoot={detail?.merkle_root');
  expect(source).toContain('hashAlgorithm={detail?.hash_algorithm');
  expect(source).toContain('artifactCount={detail?.artifact_count');
  expect(source).toContain('policySnapshot={detail?.policy_snapshot');
  expect(source).toContain('signing={detail?.signing');
});

// Strip comments so an identifier mentioned in an explanatory comment is not
// mistaken for one baked into rendered output.
function readCode(relativePath: string): string {
  return read(relativePath)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

test('2b: no screenshot identifiers or counts are hardcoded anywhere on Screen 9', () => {
  for (const file of [PANEL, VERIFICATION]) {
    const code = readCode(file);
    expect(code).not.toContain('EV-2026-017');
    expect(code).not.toContain('INC-2026-017');
    expect(code).not.toContain('POL-MINT-007');
    expect(code).not.toContain('0x8ab913');
    expect(code).not.toContain('decoda-evidence-prod-01');
    expect(code).not.toMatch(/37\s*\/\s*37/);
  }
});

test('2c: a package with no sealed Merkle root says so instead of showing a placeholder', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('Not sealed in this package');
});

/* ── 3. Package Verification panel ───────────────────────────────── */

test('3a: verification checks are rendered from the backend result, one row per category', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('export function PackageVerificationPanel');
  expect(source).toContain('const checks = result?.checks ?? []');
  expect(source).toContain('checks.map((check)');
  // Each row shows its own reason — failures are never collapsed into one flag.
  expect(source).toContain('{check.detail}');
  expect(source).toContain('failed_artifact_paths');
});

test('3b: every documented verification status has a distinct presentation', () => {
  const source = read(VERIFICATION);
  for (const status of [
    'VERIFIED',
    'PARTIALLY_VERIFIED',
    'VERIFICATION_FAILED',
    'SIGNATURE_UNAVAILABLE',
    'INCOMPLETE_PACKAGE',
    'VERIFYING',
  ]) {
    expect(source).toContain(`${status}:`);
  }
});

test('3c: only VERIFIED is presented as success; every other status is non-green', () => {
  const source = read(VERIFICATION);
  expect(source).toContain("VERIFIED: { label: 'Verified', variant: 'success'");
  expect(source).toContain("PARTIALLY_VERIFIED: { label: 'Partially Verified', variant: 'warning'");
  expect(source).toContain("VERIFICATION_FAILED: { label: 'Verification Failed', variant: 'danger'");
  expect(source).toContain("SIGNATURE_UNAVAILABLE: { label: 'Signature Unavailable', variant: 'warning'");
  expect(source).toContain("INCOMPLETE_PACKAGE: { label: 'Incomplete Package', variant: 'warning'");
});

test('3d: an unavailable check is neither a pass nor a failure', () => {
  const source = read(VERIFICATION);
  expect(source).toContain("unavailable: { glyph: '?'");
  expect(source).toContain("srLabel: 'could not be checked'");
  expect(source).toContain("not_applicable: { glyph: '–'");
});

/* ── 4. VERIFIED shield is gated on the backend conclusion ───────── */

test('4a: the green shield renders only for a backend VERIFIED status', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('export function VerificationShield');
  expect(source).toContain('const isVerified = status === VERIFIED_STATUS');
  expect(source).toContain("export const VERIFIED_STATUS = 'VERIFIED'");
});

test('4b: a package with no recorded verification never shows a success state', () => {
  const source = read(VERIFICATION);
  expect(source).toContain("if (!result) return 'This package has not been verified yet.'");
});

test('4c: HSM/KMS backing is claimed only when the backend signer says hardware_backed', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('const hardwareBacked = Boolean(result?.signer?.hardware_backed)');
  expect(source).toContain('signing?.hardware_backed ?');
  // And a verified software-key seal explicitly says it is not hardware-backed.
  expect(source).toContain('not an');
  expect(source).toContain('HSM/KMS-backed signature');
});

/* ── 5. Package Contents ─────────────────────────────────────────── */

test('5a: package contents come from the backend manifest, not the frontend', () => {
  const source = read(PANEL);
  expect(source).toContain('<PackageContents contents={detail?.package_contents');
});

test('5b: an unavailable file is shown as unavailable with its reason', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('export function PackageContents');
  expect(source).toContain('entry.unavailable_reason');
  expect(source).toContain('Unavailable');
});

test('5c: the artifact count is dynamic', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('entry.count ?? 0');
  expect(source).toContain('declared_count');
});

/* ── 6. Action row ───────────────────────────────────────────────── */

test('6a: all three primary actions exist', () => {
  const source = read(PANEL);
  expect(source).toContain('Download Evidence Package (.zip)');
  expect(source).toContain('Download Manifest (JSON)');
  expect(source).toContain('Verify Integrity');
});

test('6b: the archive download is gated by the backend allowed_actions, not by the UI alone', () => {
  const source = read(PANEL);
  expect(source).toContain('const canDownloadArchive = Boolean(');
  expect(source).toContain("detail?.allowed_actions?.download ?? false");
  expect(source).toContain('disabled={!canDownloadArchive');
});

test('6c: downloads go through the same-origin proxy, never a direct storage URL', () => {
  const source = read(PANEL);
  expect(source).toContain('`/api/exports/${encodeURIComponent(pkg.id)}/archive`');
  expect(source).toContain('`/api/exports/${encodeURIComponent(pkg.id)}/manifest`');
});

/* ── 7. Verify Integrity UX ──────────────────────────────────────── */

test('7a: the flow is IDLE -> VERIFYING -> backend result', () => {
  const source = read(PANEL);
  expect(source).toContain("const verifyPhase: VerifyPhase = verifying ? 'verifying' : 'idle'");
  expect(source).toContain('phase={verifyPhase}');
});

test('7b: a verification already in flight cannot be issued twice', () => {
  const source = read(PANEL);
  expect(source).toContain('if (verifyingId) return;');
});

test('7c: an archive build already in flight cannot be issued twice', () => {
  const source = read(PANEL);
  expect(source).toContain('if (archivingId) return;');
});

test('7d: a stale prior result is withheld while verifying or after a failed request', () => {
  const source = read(PANEL);
  expect(source).toContain('verifying || verifyError ? null : (detail?.verification_result ?? null)');
});

test('7e: package metadata is refreshed from the backend after verification', () => {
  const source = read(PANEL);
  expect(source).toContain('setReloadKey((k) => k + 1)');
});

test('7f: a failure message names the categories that failed', () => {
  const source = read(PANEL);
  expect(source).toContain('result?.failed_checks');
  expect(source).toContain('result?.unavailable_checks');
  expect(source).toContain('readableCheckName');
});

/* ── 8. Export History tab ───────────────────────────────────────── */

test('8a: export history renders real records with the expected columns', () => {
  const source = read(PANEL);
  for (const header of [
    'Package', 'Incident', 'Created', 'Artifacts', 'Merkle Root', 'Signer', 'Verification', 'Downloads',
  ]) {
    expect(source).toContain(`'${header}'`);
  }
});

test('8b: export history is paged, never an unbounded load', () => {
  const source = read(PANEL);
  expect(source).toContain('EXPORT_HISTORY_PAGE_SIZE');
  expect(source).toContain('offset: String(historyOffset)');
  expect(source).toContain('limit: String(EXPORT_HISTORY_PAGE_SIZE)');
});

test('8c: a failed history load shows an error and no rows', () => {
  const source = read(PANEL);
  expect(source).toContain('Export history could not be loaded.');
  expect(source).toContain('setHistoryRows([]);');
});

test('8d: a never-verified package is not shown as verified in history', () => {
  const source = read(PANEL);
  expect(source).toContain('verificationStatusPresentation(row.verification_status)');
  const verification = read(VERIFICATION);
  expect(verification).toContain("const NOT_VERIFIED = { label: 'Not Verified', variant: 'neutral' as PillVariant");
});

/* ── 9. Proxy routes ─────────────────────────────────────────────── */

test('9a: the archive proxy never exposes the backend URL or a signed storage URL', () => {
  const source = read(ARCHIVE_PROXY);
  expect(source).toContain('getRuntimeConfig()');
  expect(source).toContain('normalizeApiBaseUrl');
  expect(source).not.toContain('NEXT_PUBLIC_API_URL');
  expect(source).not.toContain('signed_url');
});

test('9b: the archive response is marked no-store for sensitive evidence', () => {
  const source = read(ARCHIVE_PROXY);
  expect(source).toContain("'Cache-Control': 'no-store, no-cache, must-revalidate, private, no-transform'");
  expect(source).toContain("'X-Content-Type-Options': 'nosniff'");
});

test('9c: the archive proxy requires authorization and forwards the workspace header', () => {
  const source = read(ARCHIVE_PROXY);
  expect(source).toContain("request.headers.get('authorization')");
  expect(source).toContain('missing_authorization');
  expect(source).toContain('normalizeWorkspaceHeaderValue');
});

test('9d: the history proxy forwards only bounded pagination params', () => {
  const source = read(HISTORY_PROXY);
  expect(source).toContain("backendPath: '/exports/history'");
  expect(source).toContain("for (const key of ['limit', 'offset'] as const)");
});

/* ── 10. Failure states ──────────────────────────────────────────── */

test('10a: cryptographic failure states are explicit and never replaced by Verified', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('One or more cryptographic checks FAILED');
  expect(source).toContain('must not be presented as proof');
  expect(source).toContain('Evidence this package declares is missing');
  expect(source).toContain('This is not evidence of tampering.');
});

test('10b: a filename from a response header is sanitized before it reaches a download', () => {
  const source = read(PANEL);
  expect(source).toContain('function filenameFromDisposition');
  expect(source).toContain("name === '.' || name === '..'");
});

/**
 * Screen 9 — one verification state, and a legacy validation is not it.
 *
 * The production contradiction these lock down. For one package, at once:
 *
 *   Package list       Integrity = Verified
 *   Detail header      Verified
 *   Hash Verification  Verified
 *   Detail body        "Integrity verified · 9/9 files matched · … · seal valid"
 *
 * …beside that same package's own detail reporting Files Verified 0, Integrity
 * failures 0, "Not Verified", "Never verified", no sealed Merkle root, no sealed
 * policy snapshot, and a checklist whose cryptographic rows were all unverified.
 *
 * Each of those four claims came from a place that derived verification for
 * itself: the first three from the lifecycle `integrity_status` (which an OLD
 * pre-canonical hash-check record had set to `verified`), the fourth from that
 * record rendered verbatim, in the present tense, as the current result.
 *
 * Now all of them render `verification_contract` — badge, hash_verification and
 * legacy_validation — and the legacy record appears only inside its own labelled
 * historical block.
 *
 * Source-contract tests (read the .tsx source and assert structural presence),
 * matching the existing Screen 9 spec pattern.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';
const VERIFICATION = 'app/evidence-package-verification.tsx';

/* ── The table badge ─────────────────────────────────────────────── */

test('table: the Integrity column renders the canonical contract badge', () => {
  const source = read(PANEL);
  expect(source).toContain('const integ = packageVerificationBadge(pkg)');
  // The row no longer maps a lifecycle status of its own.
  expect(source).not.toContain('const integ = integrityPill(pkg)');
});

test('table: rows carry the contract, so the badge is a backend fact', () => {
  const source = read(PANEL);
  expect(source).toContain('verification_contract?: VerificationContract | null');
});

/* ── The detail header badge ─────────────────────────────────────── */

test('detail: the header badge is the same selector the table row uses', () => {
  const source = read(PANEL);
  expect(source).toContain('packageVerificationBadge(selectedDetail ?? drawerPkg)');
  expect(source).not.toContain('integrityPill(selectedDetail ?? drawerPkg)');
});

test('detail: the Evidence Package status comes from the contract, not a second field', () => {
  const source = read(PANEL);
  expect(source).toContain("verificationContract?.overall_status ?? null");
  // The separate verification_result_status is no longer a status source here.
  expect(source).not.toContain('detail?.verification_result?.status ?? detail?.verification_result_status');
});

/* ── Hash Verification ───────────────────────────────────────────── */

test('detail: Hash Verification is the contract axis, never inferred from hashes', () => {
  const source = read(PANEL);
  expect(source).toContain('contractHashVerification(verificationContract)');
  expect(source).not.toContain('const hashStatus = hashVerification(source);');
});

test('hash verification: only a recomputed-and-matched result reads Verified', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('export function contractHashVerification');
  expect(source).toContain('hash_verification?: { state: string; label: string; variant: string; verified?: boolean }');
});

/* ── The legacy summary sentence ─────────────────────────────────── */

test('detail: the false present-tense legacy summary is gone', () => {
  const source = read(PANEL);
  // The exact expressions that rendered it (comments quoting the old copy are
  // documentation, so these target the interpolations themselves).
  expect(source).not.toContain('`Integrity verified · ${');
  expect(source).not.toContain('files matched`');
  expect(source).not.toContain('${verification.files_verified');
  expect(source).not.toContain('verification.seal_status');
  // And the raw persisted record is not read by the panel at all.
  expect(source).not.toContain('const verification = detail?.verification ?? null');
});

test('detail: a legacy record renders in its own labelled historical block', () => {
  const source = read(PANEL);
  expect(source).toContain('<LegacyValidationRecord legacy={verificationContract?.legacy_validation} />');
});

test('legacy record: stated in the past tense, with its schema, and never as a seal claim', () => {
  const verification = read(VERIFICATION);
  expect(verification).toContain('export function LegacyValidationRecord');
  expect(verification).toContain('Legacy Validation Record');
  expect(verification).toContain('hashes matched');
  expect(verification).toContain('legacy schema ');
  // "seal valid" claimed a Merkle commitment these packages never sealed, so no
  // seal state is projected into the historical block or rendered from it.
  expect(verification).not.toContain('legacy.seal_status');
  expect(verification).not.toContain('seal_status?:');
});

/* ── Why a package is short of VERIFIED ──────────────────────────── */

test('detail: a legacy-schema package states the reason it is not fully verified', () => {
  const source = read(PANEL);
  expect(source).toContain('verificationContract?.legacy_schema?.legacy');
  expect(source).toContain('verificationContract.legacy_schema.reason');
});

test('legacy schema: the contract type carries the sealed facts the manifest lacks', () => {
  const verification = read(VERIFICATION);
  expect(verification).toContain('missing_sealed_facts?: string[]');
  expect(verification).toContain('required_schema_version?: string | null');
});

/* ── One green gate, everywhere ──────────────────────────────────── */

test('truthful: the legacy integrity state is never coloured as a success', () => {
  const source = read(PANEL);
  expect(source).toContain("legacy_hash_validated: 'warning'");
  expect(source).not.toContain("legacy_hash_validated: 'success'");
  // And it is a selectable state on the ordinary integrity filter.
  expect(source).toContain("{ value: 'legacy_hash_validated', label: 'Legacy Hash Validated' }");
});

test('truthful: the integrity-failed alert is raised from the contract', () => {
  const source = read(PANEL);
  expect(source).toContain("['VERIFICATION_FAILED', 'INCOMPLETE_PACKAGE'].includes(verificationContract.overall_status)");
  expect(source).toContain("verificationContract.artifact_hashes?.hash_failures");
});

test('truthful: no surface hardcodes a verified badge or an artifact count', () => {
  const source = read(PANEL);
  const verification = read(VERIFICATION);
  for (const file of [source, verification]) {
    // No badge, shield or hash-verification value is ever literal 'Verified'
    // in the frontend — every one of them arrives from the backend contract.
    expect(file).not.toMatch(/badge:\s*\{\s*label:\s*'Verified'/);
    expect(file).not.toMatch(/hash_verification:\s*\{\s*label:\s*'Verified'/);
    // Counts are rendered from the contract's own numbers, never a literal.
    expect(file).not.toMatch(/>\s*9\s*\/\s*9\s*</);
  }
});

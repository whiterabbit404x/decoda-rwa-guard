/**
 * Screen 9 — every surface renders ONE canonical verification result.
 *
 * The production contradiction these lock down: for a selected package the table
 * showed Integrity = Verified, the Crypto-Auditing Clerk showed Files Verified 9
 * / Integrity Failures 0, and the Verification Checklist showed "Hashes verified
 * ✗" — three surfaces, three independent derivations of one fact.
 *
 * The checklist was read from `completeness.checklist`, a snapshot frozen into
 * the package summary at BUILD time (before any verification could have run).
 * Now every surface renders `verification_contract`, built by the backend.
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

/* ── One backend result, four surfaces ───────────────────────────── */

test('contract: the detail response carries the canonical verification contract', () => {
  const source = read(PANEL);
  expect(source).toContain('verification_contract?: VerificationContract | null');
  expect(source).toContain('type VerificationContract');
});

test('contract: the Clerk sidebar reads the contract, never its own calculation', () => {
  const source = read(PANEL);
  expect(source).toContain('const contract = detail?.verification_contract ?? null');
  expect(source).toContain('const checklist = contract?.checklist ?? []');
  expect(source).toContain('const artifactHashes = contract?.artifact_hashes ?? null');
  // The stale build-time checklist is gone from the sidebar entirely.
  expect(source).not.toContain('completeness?.checklist');
});

test('contract: the package detail view renders the same contract as the sidebar', () => {
  const source = read(PANEL);
  expect(source).toContain('detail?.verification_contract ?? null');
  expect(source).toContain('contract={verificationContract}');
  expect(source).toContain('checklist={verificationContract.checklist}');
});

test('contract: the table Integrity badge and the detail badge share one source', () => {
  const source = read(PANEL);
  // The drawer header renders the SAME integrityPill the table row renders, off
  // the backend's canonical integrity_status / integrity_label.
  expect(source).toContain('integrityPill(selectedDetail ?? drawerPkg)');
  expect(source).toContain('pkg.integrity_label');
});

/* ── Files Hashed is not Files Verified ──────────────────────────── */

test('contract: Files Hashed and Files Verified are distinct backend facts', () => {
  const source = read(PANEL);
  expect(source).toContain('artifactHashes?.files_hashed');
  expect(source).toContain('artifactHashes?.files_verified');
  // Neither is inferred in the browser from the other.
  expect(source).not.toMatch(/files_verified\s*=\s*.*files_hashed/);
  expect(source).toContain('recomputed on the server and MATCHED');
});

test('contract: Integrity Failures comes from the contract, not the raw record', () => {
  const source = read(PANEL);
  expect(source).toContain('artifactHashes?.hash_failures');
  expect(source).not.toContain('detail?.verification?.files_failed?.length');
});

/* ── Tri-state checklist ─────────────────────────────────────────── */

test('checklist: an unrun check renders "○ not verified", never a red cross', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('export function VerificationChecklist');
  expect(source).toContain("not_verified: { glyph: '○'");
  expect(source).toContain('not verified yet');
  // The three other truthful marks stay distinct.
  expect(source).toContain("passed: { glyph: '✓'");
  expect(source).toContain("failed: { glyph: '✕'");
  expect(source).toContain("unavailable: { glyph: '?'");
});

test('checklist: rows are rendered from the backend, never invented client-side', () => {
  const source = read(VERIFICATION);
  // The component maps whatever rows the contract supplies; it declares none of
  // its own, so a check the backend does not run can never appear.
  expect(source).toContain('const rows = checklist ?? []');
  expect(source).toContain('rows.map((item)');
  expect(source).not.toContain("label: 'Merkle root matches'");
  expect(source).not.toContain("label: 'Manifest signature valid'");
});

test('checklist: a never-verified package says so instead of showing failures', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('Cryptographic checks have not been run for this package yet');
});

/* ── The shield is gated on the backend status alone ─────────────── */

test('shield: green requires the backend shield state, not a completeness score', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('const isVerified = shieldState === VERIFIED_STATUS');
  expect(source).toContain('SHIELD_PRESENTATION');
  expect(source).toContain('READY_FOR_VERIFICATION');
  expect(source).toContain('INTEGRITY_CHECK_FAILED');
});

test('shield: a stale prior result never describes an in-flight verification', () => {
  const source = read(PANEL);
  expect(source).toContain("verifying || verifyError ? null : (detail?.verification_contract ?? null)");
});

/* ── Verify Integrity: real server call, single flight, full refresh ── */

test('verify: the button calls the backend and cannot be double-fired', () => {
  const source = read(PANEL);
  expect(source).toContain('/verify');
  expect(source).toContain("method: 'POST'");
  // A verification already in flight drops the second click.
  expect(source).toContain('if (verifyingId) return;');
  expect(source).toContain('disabled={!detailCanVerify || !!verifying}');
  expect(source).toContain("{verifying ? 'Verifying…' : 'Verify Integrity'}");
});

test('verify: completion refreshes every surface without a page reload', () => {
  const source = read(PANEL);
  // One reload key refetches the list (table badge + metrics) and the detail
  // (contract, checklist, Clerk counters, Last Verified).
  expect(source).toContain('setReloadKey((k) => k + 1)');
  expect(source).toContain('contract.verified_at');
});

/* ── View Package ────────────────────────────────────────────────── */

test('view: the package detail opens in a drawer over the list, with a way back', () => {
  const source = read(PANEL);
  expect(source).toContain('className="drawerOverlay"');
  expect(source).toContain('drawerCard drawerCardWide');
  expect(source).toContain('aria-modal="true"');
  expect(source).toContain('Back to Evidence Packages');
  expect(source).toContain("event.key === 'Escape'");
  // The list is never permanently replaced: the drawer is additive state.
  expect(source).toContain('const [packageDrawerId, setPackageDrawerId] = useState');
});

test('view: closing the drawer keeps the row selected for the Clerk', () => {
  const source = read(PANEL);
  expect(source).toContain("const closePackageDrawer = useCallback(() => setPackageDrawerId('')");
  // Selection state is untouched by closing, and there is a way back in.
  expect(source).toContain('{selectedPkg && !packageDrawerId && (');
  expect(source).toContain('onClick={() => openPackage(selectedPkg.id)}');
});

test('view: the detail exposes the package downloads and Verify Integrity', () => {
  const source = read(PANEL);
  expect(source).toContain('Download Evidence Package');
  expect(source).toContain('Download Manifest (JSON)');
  expect(source).toContain('Verify Integrity');
  // RBAC stays backend-authoritative for every one of them.
  expect(source).toContain('allowed_actions');
  expect(source).toContain('can_export');
});

test('view: package contents come from the backend manifest only', () => {
  const source = read(PANEL);
  const verification = read(VERIFICATION);
  expect(source).toContain('<PackageContents contents={detail?.package_contents ?? null} />');
  expect(verification).toContain('export function PackageContents');
  // A file that is not produced is shown as unavailable with a reason.
  expect(verification).toContain('unavailable_reason');
});

/* ── The agent stays advisory ────────────────────────────────────── */

test('agent: the Clerk explains the deterministic result, it does not decide it', () => {
  const source = read(PANEL);
  expect(source).toContain('The Clerk is ADVISORY');
  // No pass/fail arithmetic anywhere in the sidebar.
  expect(source).not.toMatch(/hashes_verified\s*=\s*[^;]*===/);
});

/* ── No hardcoded package, count or badge ────────────────────────── */

test('truthful: no package number, artifact count or verified badge is hardcoded', () => {
  const source = read(PANEL);
  const verification = read(VERIFICATION);
  for (const file of [source, verification]) {
    expect(file).not.toContain('EV-2026-007');
    expect(file).not.toContain('9 / 9');
    expect(file).not.toMatch(/label:\s*'Verified',\s*variant:\s*'success'\s*\}\s*;\s*\/\/\s*always/);
  }
});

/**
 * Source-level guardrails for the public /trust page.
 *
 * Trust claims must not contradict observable evidence:
 *   - no unverifiable "every push" CI claim (the only workflow is manual),
 *   - no perfect-readiness score,
 *   - no "Live EVM telemetry proven" style claim while Base-first,
 *   - no prominent link into the de-emphasised Live Proof page,
 *   - SOC 2 is disclosed as not-yet-certified.
 *
 * The cryptographic/security block below is the output of the trust-claims
 * audit. Each prohibition names the file that makes the phrase false, so a
 * future change that genuinely implements the control can retire the test
 * deliberately rather than by deleting an assertion it does not understand.
 * A phrase is prohibited ONLY where the implementation does not justify it.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/**
 * What a customer can actually read on the rendered page.
 *
 * Two transforms, both load-bearing:
 *
 *  1. Comments are stripped. A source comment explaining WHY a phrase is
 *     banned must not itself trip the ban — otherwise the only way to document
 *     a prohibition is to not document it.
 *  2. Adjacent string-literal concatenations are joined, so a claim split as
 *     `'... rather ' + 'than a credential'` is matched as the one sentence the
 *     customer sees rather than as two fragments neither of which matches.
 */
function customerVisibleText(filePath: string[]): string {
  return fs
    .readFileSync(path.join(__dirname, '..', ...filePath), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ')
    .replace(/'\s*\+\s*'/g, '')
    .replace(/"\s*\+\s*"/g, '');
}

const src = customerVisibleText(['app', 'trust', 'page.tsx']);
const securitySrc = customerVisibleText(['app', 'security', 'page.tsx']);
const homeSrc = customerVisibleText(['app', 'components', 'home', 'home-data.ts']);
const privacySrc = customerVisibleText(['app', 'privacy', 'page.tsx']);

test('no unverifiable "every push" CI proof claim', () => {
  expect(src).not.toContain('Every push triggers');
});

test('no perfect production-readiness score claim', () => {
  expect(src).not.toContain('100/100');
  expect(src).not.toContain('100 percent');
});

test('no "Live EVM telemetry proven" style claim', () => {
  expect(src).not.toContain('Live EVM telemetry proven');
  expect(src).not.toContain('telemetry proven');
});

test('Trust does not prominently link the de-emphasised Live Proof page', () => {
  expect(src).not.toContain('/live-proof');
  expect(src).not.toContain('Live Proof');
});

test('SOC 2 is disclosed as not yet certified', () => {
  expect(src).toContain('not yet certified');
  expect(src).toContain('Not yet');
});

test('fail-closed release proof gate is described without an absolute proven claim', () => {
  expect(src).toContain('Fail-closed release proof gates');
  expect(src).toContain('unverified claim');
});

test('staff access is described truthfully, with its exclusions stated', () => {
  // The claim must be the narrow one the implementation supports: recorded and
  // customer-visible for CUSTOMER-SPECIFIC access, with the cross-tenant
  // directory read explicitly excluded rather than quietly included.
  expect(src).toContain('Can Decoda staff see our data?');
  expect(src).toContain('no impersonation or log-in-as');
  expect(src).toContain('without ordinary membership of your workspace');
  expect(src).toContain('are recorded internally and are not shown in any one customer');
  // Never the absolute version of the claim.
  expect(src).not.toContain('Every action Decoda staff');
  expect(src).not.toContain('all staff activity is visible');
});


/* ── Cryptographic and security claim guardrails ──────────────────── */

test('no envelope-encryption claim: secret_crypto encrypts directly under one managed key', () => {
  // services/api/app/secret_crypto.py::encrypt_secret takes the plaintext to
  // AES-256-GCM under a single key from load_managed_key('ENCRYPTION'). There
  // is no data key, and nothing wraps a key with another key. "Envelope
  // encryption" would require a DEK encrypted by a KEK.
  expect(src).not.toMatch(/envelope encryption/i);
  expect(securitySrc).not.toMatch(/envelope encryption/i);
});

test('secret encryption is described as AES-256-GCM under a managed, versioned key', () => {
  expect(src).toContain('AES-256-GCM under a managed, versioned application key');
});

test('no end-to-end encryption claim', () => {
  // Decoda decrypts and processes workspace secrets and telemetry server-side.
  // E2EE would mean it could not.
  expect(src).not.toMatch(/end-to-end encrypt|\bE2EE\b/i);
  expect(securitySrc).not.toMatch(/end-to-end encrypt|\bE2EE\b/i);
});

test('no TLS version or cipher claim: no Decoda code sets one', () => {
  // TLS is terminated by Vercel/Railway. The FastAPI app installs no
  // HTTPSRedirectMiddleware and railway.json carries no TLS settings, so no
  // TLS version is provable from this repository in any deployment.
  for (const source of [src, securitySrc, privacySrc]) {
    expect(source).not.toMatch(/TLS 1\.[0-3]/);
  }
});

test('no unconditional "all API traffic uses TLS" claim', () => {
  // Application code does not enforce it; the platform provides it.
  expect(src).not.toContain('All API traffic uses TLS');
  expect(src).toContain('served over HTTPS by the deployment platform');
});

test('no claim that internal service communication is authenticated', () => {
  // services/api/app/main.py::request_json sends only Content-Type. The
  // analysis services declare no auth dependency. Network isolation is the
  // only control, and the page must say so.
  expect(src).not.toContain('Internal service communication is authenticated');
  expect(src).toContain('rather than by an application-level credential');
});

test('no server-side-encryption-at-rest claim for evidence objects', () => {
  // export_storage.py::write_bytes calls put_object() with no
  // ServerSideEncryption argument, so encryption is the bucket's, not ours.
  expect(src).not.toContain('stored with server-side encryption at rest');
  expect(src).toContain('Encryption at rest and');
});

test('no short-lived or auto-rotating database credential claim', () => {
  // credential_rotation.py::SUPPORTED_CREDENTIAL_TYPES contains no database
  // credential type at all, and pg_connection() uses a static DSN.
  expect(src).not.toMatch(/short-lived/i);
  expect(src).not.toContain('Database connections use short-lived credentials');
  expect(src).toContain('Database credentials are not on an automated rotation schedule');
});

test('no HSM, KMS-custody or hardware-backed claim for the signing key', () => {
  // The Ed25519 seed is loaded into application memory. Every signer in this
  // build reports hardware_backed=False.
  for (const source of [src, securitySrc, homeSrc]) {
    expect(source).not.toMatch(/\bHSM\b|hardware-backed|non-exportable/i);
  }
});

test('evidence authenticity is conditional on Ed25519 being provisioned', () => {
  // evidence_signing.py::_ed25519_signature_for returns None when no key is
  // configured, and nothing enforces the key in production, so the page must
  // not state that every new package is signed.
  expect(src).toContain('where a deployment has');
  expect(src).not.toContain('New evidence packages are signed with Decoda');
});

test('evidence integrity is claimed as offline-verifiable, which needs no key', () => {
  expect(src).toContain('detectable offline');
  expect(src).toContain('Merkle integrity root');
});

test('no immutable-evidence or immutable-audit claim on any public surface', () => {
  // Tamper-EVIDENT is not immutable: the retention worker may delete audit
  // rows once their period expires, and object storage WORM is not set by us.
  for (const source of [src, securitySrc, homeSrc]) {
    expect(source).not.toMatch(/immutable/i);
  }
  expect(src).toContain('Append-only, hash-chained audit logs');
});

test('audit retention is disclosed rather than implying permanent records', () => {
  // Must not contradict /privacy, which publishes a 365-day audit period.
  expect(src).toContain('retention period published in the Privacy Policy');
});

test('no compliance certification or regulator-approval claim', () => {
  // Deliberately NOT a blanket ban on the string "SOC 2 certified": the page
  // asks "Is Decoda SOC 2 certified?" and answers "Not yet." Banning the
  // phrase outright would delete the honest disclosure along with the
  // overclaim. What is banned is every AFFIRMATIVE form.
  for (const source of [src, securitySrc, homeSrc]) {
    expect(source).not.toMatch(/we are SOC ?2|Decoda is SOC ?2|SOC ?2[- ]compliant|SOC ?2 Type ?I+ certified/i);
    expect(source).not.toMatch(/ISO ?27001|FedRAMP|PCI ?DSS|HIPAA[- ]compliant/i);
    expect(source).not.toMatch(/regulator[- ]approved|regulator[- ]ready|Big Four|audit[- ]ready/i);
    expect(source).not.toMatch(/fully compliant|certified compliant/i);
  }
  // And the honest disclosure must still be there.
  expect(src).toContain('Not yet');
  expect(src).toContain('not yet certified');
});

test('no absolute zero-risk or zero-asset-control superlative', () => {
  for (const source of [src, securitySrc, homeSrc]) {
    expect(source).not.toMatch(/zero operational risk|zero asset control|guaranteed/i);
  }
});

test('MFA is described as a session control, never as a token-theft control', () => {
  // docs/PILOT_MFA_BOUNDARY.md §11 states explicitly that it is not one.
  expect(src).toContain('it does not make a stolen session token harmless');
  expect(src).not.toMatch(/MFA prevents token theft|prevents session hijack/i);
});

test('Pilot execution boundary is scoped to Pilot, not claimed platform-wide', () => {
  expect(src).toContain('Pilot workspaces additionally cannot execute production blockchain state');
  expect(src).not.toMatch(/Decoda has zero asset control/i);
});

test('telemetry privacy is not overstated', () => {
  // Both of these are explicitly listed as not-to-be-claimed in
  // docs/TELEMETRY_PRIVACY_AND_REDACTION.md.
  expect(src).not.toMatch(/no sensitive data (ever )?reaches Decoda/i);
  expect(src).not.toMatch(/all telemetry is anonymi[sz]ed/i);
  expect(src).toContain('does reach Decoda before the sanitizer runs');
});

test('private-key wording stays absolute because the implementation is', () => {
  expect(src).toContain('does not request, ingest or store customer wallet private keys');
});

test('/security page does not still advertise MFA as optional', () => {
  // Mandatory for every human user of a Pilot workspace since the MFA boundary.
  expect(securitySrc).not.toContain('optional MFA');
});

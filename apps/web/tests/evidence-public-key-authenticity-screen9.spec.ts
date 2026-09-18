/**
 * Screen 9 — integrity and AUTHENTICITY are different guarantees.
 *
 * The rule these guard: a package whose hashes all match but whose only seal is
 * a shared-secret HMAC proves nothing about WHO produced it. The surface must
 * never render it the same as a package carrying a public-key signature an
 * auditor can verify offline — and it must never derive that distinction
 * itself, only render what the backend concluded.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const VERIFICATION = 'app/evidence-package-verification.tsx';
const TRUST_PAGE = 'app/trust/page.tsx';

test('1: authenticity is read from the backend contract, never derived on this surface', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('const authenticity = contract?.authenticity ?? result?.authenticity');
  expect(source).toContain('independently_verifiable');
  // It must not invent the flag from the signer metadata or the seal shape.
  expect(source).not.toContain("algorithm === 'Ed25519'");
  expect(source).not.toContain('seal?.signatures');
});

test('2: a verified public-key package says it is verifiable offline with the published key', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('if (isVerified && independentlyVerifiable)');
  expect(source).toContain('verify offline');
  expect(source).toContain('published verification key');
});

test('3: a verified legacy HMAC package is explicitly NOT independently verifiable', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('{isVerified && !independentlyVerifiable ?');
  expect(source).toContain('Legacy ');
  expect(source).toContain('not independently verifiable');
  expect(source).toContain('outside Decoda');
});

test('4: the two states are separate branches, so they cannot render the same badge', () => {
  const source = read(VERIFICATION);
  const legacyBranch = source.indexOf('{isVerified && !independentlyVerifiable ?');
  const publicKeyBranch = source.indexOf('{isVerified && independentlyVerifiable && !hardwareBacked ?');
  expect(legacyBranch).toBeGreaterThan(-1);
  expect(publicKeyBranch).toBeGreaterThan(-1);
  expect(legacyBranch).not.toEqual(publicKeyBranch);
});

test('5: a public-key signature is never rendered as hardware custody', () => {
  const source = read(VERIFICATION);
  // Ed25519 buys independent verifiability, not an HSM. Both must be stated.
  expect(source).toContain('publicly verifiable, but not an');
  expect(source).toContain('HSM/KMS-backed signature');
});

test('6: the metadata panel reports offline verifiability and the verification key id', () => {
  const source = read(VERIFICATION);
  expect(source).toContain('label="Offline Verification"');
  expect(source).toContain('Public-key verifiable');
  expect(source).toContain('Not independently verifiable (legacy seal)');
  expect(source).toContain('label="Verification Key"');
});

test('7: the trust page claims offline verifiability and does not claim immutability', () => {
  const source = read(TRUST_PAGE);
  expect(source).toContain('verified offline');
  expect(source).toContain('public verification key');
  expect(source).toContain('does not require access to');
  // "immutable evidence" overstates a tamper-EVIDENT guarantee.
  expect(source).not.toContain('immutable evidence');
  expect(source).not.toContain('cannot be retroactively altered');
});

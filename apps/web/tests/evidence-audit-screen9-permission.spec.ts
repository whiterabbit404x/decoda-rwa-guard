/**
 * Screen 9: "Create Evidence Package" — explicit permission-state regression.
 *
 * Source-contract tests (repo convention): read the panel .tsx and assert on the
 * structural presence of the state machine that drives the primary Create button.
 *
 * These lock in the fix for the recurring "button is still disabled for a workspace
 * admin" report. The button now resolves through FOUR distinct, truthful states so a
 * faded button always has exactly one honest cause, and an API/permission-read failure
 * is never rendered as a confirmed authorization denial:
 *
 *   - Loading  → label "Checking permission…", disabled temporarily
 *   - Authorized → label "Create Evidence Package", enabled
 *   - Denied   → disabled, tooltip "You do not have permission to create evidence packages."
 *   - Error    → "Permission could not be verified." + a Retry action (NOT a denial)
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';

/** The single line that composes the page-level button's disabled condition. */
function createDisabledLine(source: string): string {
  return source.split('\n').find((l) => l.includes('const createDisabled =')) ?? '';
}

/* ── can_export: true ENABLES the button ──────────────────────────────────────── */

test('can_export: true enables the button (never in the disable set)', () => {
  const source = read(PANEL);
  // Only an explicit `false` denies; a `true` is never a disable term anywhere in the
  // composed condition, so an authorized admin's button is enabled.
  expect(source).toContain('const permissionDenied = !permissionResolving && !loadError && canExport === false');
  const line = createDisabledLine(source);
  expect(line).toContain('const createDisabled = creating || permissionResolving || permissionUndetermined || permissionDenied');
  // Guard against a regression to a truthy coercion that would treat true/undefined wrongly.
  expect(source).not.toContain('disabled={!canExport}');
  // The authorized label is the plain call to action.
  expect(source).toContain("'Create Evidence Package'");
});

/* ── can_export: false DISABLES the button ────────────────────────────────────── */

test('can_export: false disables the button and shows the denial tooltip', () => {
  const source = read(PANEL);
  expect(source).toContain('canExport === false');
  expect(createDisabledLine(source)).toContain('permissionDenied');
  expect(source).toContain('You do not have permission to create evidence packages.');
});

/* ── undefined during initial load → enabled once the API returns true ────────── */

test('initial undefined can_export becomes enabled after the API returns true', () => {
  const source = read(PANEL);
  // The field is kept as a tri-state — an absent field stays undefined, never coerced
  // to a denial. Denial strictly requires an explicit `false`, so undefined is enabled.
  expect(source).toContain("setCanExport(typeof p.can_export === 'boolean' ? p.can_export : undefined)");
  expect(source).toContain('useState<boolean | undefined>(undefined)');
  // "Resolving" is bounded to the in-flight load and clears when it finishes, so an
  // undefined-then-true transition ends enabled (not stuck "checking", not denied).
  expect(source).toContain('const permissionResolving = runtimeLoading || dataLoading');
  expect(source).toContain('const permissionDenied = !permissionResolving && !loadError && canExport === false');
});

/* ── API failure → Retry, never a permanent denial ────────────────────────────── */

test('an API/permission-read failure shows Retry, not a confirmed denial', () => {
  const source = read(PANEL);
  // The failed-read state is DISTINCT from a denial: it is derived from loadError, not
  // from canExport === false.
  expect(source).toContain('const permissionUndetermined = !permissionResolving && Boolean(loadError)');
  // It is surfaced explicitly and recoverably next to the button.
  expect(source).toContain('Permission could not be verified.');
  // The explicit-error region carries a Retry that re-runs the load — tie the Retry to
  // the message, not to some other Retry elsewhere on the page.
  const idx = source.indexOf('Permission could not be verified.');
  expect(idx).toBeGreaterThan(-1);
  const region = source.slice(idx, idx + 400);
  expect(region).toContain('Retry');
  expect(region).toContain('setReloadKey((k) => k + 1)');
  // An error must never be laundered into the denial branch.
  expect(source).not.toContain('permissionDenied = !permissionResolving && Boolean(loadError)');
});

/* ── An empty incident list does NOT disable the page-level button ────────────── */

test('an empty incident list does not disable the page-level button', () => {
  const source = read(PANEL);
  const line = createDisabledLine(source);
  // The disable condition is composed purely of permission/submission states — no
  // chain-readiness, selection, or list-emptiness terms.
  expect(line).toContain('creating');
  expect(line).not.toContain('incidentOk');
  expect(line).not.toContain('responseActionOk');
  expect(line).not.toContain('packageExists');
  expect(line).not.toContain('selectedPkg');
  expect(source).not.toContain('const canCreatePackage');
  expect(source).not.toContain('chainReadyForPackage');
});

/* ── The button opens the creation modal for an authorized admin ──────────────── */

test('the button opens the creation modal for an authorized admin', () => {
  const source = read(PANEL);
  expect(source).toContain('onClick={() => setShowCreateModal(true)}');
  expect(source).toContain('showCreateModal && (');
  expect(source).toContain('<EvidencePackageCreateModal');
});

/* ── The four explicit, truthful button states are all present ────────────────── */

test('the button exposes four explicit states with truthful labels', () => {
  const source = read(PANEL);
  // Loading.
  expect(source).toContain("'Checking permission…'");
  // Authorized.
  expect(source).toContain("'Create Evidence Package'");
  // Denied.
  expect(source).toContain('You do not have permission to create evidence packages.');
  // Error (permission could not be verified) — a recoverable, non-denial state.
  expect(source).toContain('Permission could not be verified.');
  // The label itself is the three-way state expression.
  expect(source).toContain(
    "permissionResolving ? 'Checking permission…' : creating ? 'Creating…' : 'Create Evidence Package'",
  );
});

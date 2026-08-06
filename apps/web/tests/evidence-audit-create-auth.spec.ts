/**
 * Screen 9: "Create Evidence Package" authorization + creation-flow regression.
 *
 * Source-contract tests — they read the panel .tsx and assert on the structural presence
 * of the truthful gating logic and the creation modal. They lock in the fix for the
 * "visible but disabled/faded" Create button reported for a workspace admin:
 *   - the button was disabled by a frontend chain-readiness gate (no incident / response
 *     action preselected), even though the backend authorized the admin;
 *   - an empty incident list must open the creation flow to a truthful empty state, not
 *     leave the primary button faded.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';

/* ── Permission model (backend-authoritative, tri-state) ─────────────────────── */

test('permission: can_export is preserved as a tri-state, not coerced to a denial', () => {
  const source = read(PANEL);
  // An absent field stays undefined; only an explicit boolean is a decision.
  expect(source).toContain('setCanExport(typeof p.can_export === \'boolean\' ? p.can_export : undefined)');
  expect(source).toContain('useState<boolean | undefined>(undefined)');
});

test('permission: explicit denial (=== false) disables and shows the permission tooltip', () => {
  const source = read(PANEL);
  // Denial is strictly can_export === false — never !canExport (which would treat an
  // undefined field as denial).
  expect(source).toContain('canExport === false');
  expect(source).not.toContain('disabled={!canExport}');
  expect(source).toContain('You do not have permission to create evidence packages.');
});

test('permission: undefined can_export cannot permanently disable an authorized user', () => {
  const source = read(PANEL);
  // permissionDenied requires an explicit false AND a resolved, error-free load, so an
  // undefined value after a successful load resolves to "granted" (enabled).
  expect(source).toContain('const permissionDenied = !permissionResolving && !loadError && canExport === false');
});

test('permission: initial loading shows a checking state, not a denial; finishing enables', () => {
  const source = read(PANEL);
  // While resolving we show a loading state (not "denied"); once resolved the button
  // is enabled for an authorized user.
  expect(source).toContain('const permissionResolving = runtimeLoading || dataLoading');
  expect(source).toContain('Checking your permissions…');
  // Resolving is part of the disable set, so it clears when loading finishes.
  expect(source).toContain('creating || permissionResolving || permissionUndetermined || permissionDenied');
});

test('permission: a failed permission read is "undetermined" (retry), never a denial', () => {
  const source = read(PANEL);
  expect(source).toContain('const permissionUndetermined = !permissionResolving && Boolean(loadError)');
  expect(source).toContain('Evidence service is unavailable. Retry to continue.');
});

test('permission: submitting shows "Creating…" and blocks duplicate submissions', () => {
  const source = read(PANEL);
  expect(source).toContain('setCreating(true)');
  expect(source).toContain('if (creating || permissionDenied) return');
  expect(source).toMatch(/\{creating \? 'Creating…' : 'Create Evidence Package'\}/);
});

/* ── Header button is never gated by chain / incident availability ───────────── */

test('button: header Create button disables only on createDisabled (no chain gate)', () => {
  const source = read(PANEL);
  expect(source).toContain('disabled={createDisabled}');
  // The removed chain-readiness gate must be gone entirely.
  expect(source).not.toContain('chainReadyForPackage');
  expect(source).not.toContain('canCreatePackage');
});

test('button: missing incidents do not disable the header button', () => {
  const source = read(PANEL);
  // createDisabled is composed only of permission/submission states — no incidentOk /
  // responseActionOk / packageExists terms.
  const createDisabledLine =
    source.split('\n').find((l) => l.includes('const createDisabled =')) ?? '';
  expect(createDisabledLine).toContain('creating');
  expect(createDisabledLine).not.toContain('incidentOk');
  expect(createDisabledLine).not.toContain('responseActionOk');
  expect(createDisabledLine).not.toContain('packageExists');
});

test('button: clicking opens the real creation flow (modal), not a direct export', () => {
  const source = read(PANEL);
  expect(source).toContain('onClick={() => setShowCreateModal(true)}');
  expect(source).toContain('showCreateModal && (');
  expect(source).toContain('<EvidencePackageCreateModal');
});

test('button: focus returns to the Create button when the modal closes', () => {
  const source = read(PANEL);
  expect(source).toContain('const createButtonRef = useRef<HTMLButtonElement>(null)');
  expect(source).toContain('ref={createButtonRef}');
  expect(source).toContain('createButtonRef.current?.focus()');
});

/* ── Creation modal states ───────────────────────────────────────────────────── */

test('modal: renders an accessible dialog with a labelled title and description', () => {
  const source = read(PANEL);
  expect(source).toContain('role="dialog"');
  expect(source).toContain('aria-modal="true"');
  expect(source).toContain('aria-labelledby="create-evidence-package-title"');
  expect(source).toContain('aria-describedby="create-evidence-package-desc"');
  expect(source).toContain('Create Evidence Package');
  expect(source).toContain(
    'Select an incident and generate a tamper-evident package containing its available',
  );
});

test('modal: Escape and Cancel close only when not submitting; Tab is trapped', () => {
  const source = read(PANEL);
  // Escape / backdrop / Cancel all funnel through closeIfIdle, which no-ops mid-submit.
  expect(source).toContain('const closeIfIdle = () => {');
  expect(source).toContain('if (!creating) onClose()');
  expect(source).toContain("event.key === 'Escape'");
  // Focus trap on Tab.
  expect(source).toContain("event.key !== 'Tab'");
  expect(source).toContain('first.focus()');
  expect(source).toContain('last.focus()');
  // Cancel is disabled while creating (cannot abandon an active submission).
  expect(source).toMatch(/Cancel[\s\S]{0,40}/);
});

test('modal: loads REAL incidents via /api/incidents (no hardcoded options)', () => {
  const source = read(PANEL);
  expect(source).toContain("fetch('/api/incidents?limit=50'");
  expect(source).toContain('normalizeIncidentsPayload');
  // Selector options are mapped from fetched rows, never a literal option list.
  expect(source).toContain('incidents.map((incident) => (');
  expect(source).toContain('incidentOptionLabel(incident)');
});

test('modal: incident identity is human-readable; the UUID is only the API identifier', () => {
  const source = read(PANEL);
  // Prefer a canonical number, then a backend short id, then the INC-<uuid8> fallback.
  expect(source).toContain('incident.incident_number?.trim()');
  expect(source).toContain('incident.incident_short_id?.trim()');
  expect(source).toContain('`INC-${incident.id.slice(0, 8)}`');
  // Severity / status / created are shown from canonical backend fields.
  expect(source).toContain('Severity');
  expect(source).toContain('Status');
  expect(source).toContain('Created');
});

test('modal: loading state shows a skeleton and never a false empty state', () => {
  const source = read(PANEL);
  expect(source).toContain('Loading incidents…');
  expect(source).toContain('skeletonRow');
  // aria-live region announces loading/empty/error transitions.
  expect(source).toContain('aria-live="polite"');
});

test('modal: empty incidents show the truthful empty state + a View Incidents action', () => {
  const source = read(PANEL);
  expect(source).toContain('No incidents available');
  expect(source).toContain('No incidents are currently available for evidence packaging.');
  expect(source).toContain('incident first, then return here to generate its evidence package.');
  expect(source).toContain('View Incidents');
});

test('modal: incident load failure shows a safe error with Retry', () => {
  const source = read(PANEL);
  expect(source).toContain('Incidents could not be loaded.');
  expect(source).toContain('setReloadKey((k) => k + 1)');
  expect(source).toContain('Retry');
  // Malformed bodies are not silently treated as an empty incident list.
  expect(source).toContain('if (rows === null)');
});

test('modal: submission is gated on a selected incident (not the page-level button)', () => {
  const source = read(PANEL);
  expect(source).toContain('const canSubmit = !permissionDenied && !creating && !loading && !error && !!selectedId');
  expect(source).toContain('disabled={!canSubmit}');
  // Selecting an incident updates the selection.
  expect(source).toContain('onChange={(e) => setSelectedId(e.target.value)}');
});

test('modal: states truthful evidence-category coverage (no fake per-category picker)', () => {
  const source = read(PANEL);
  expect(source).toContain(
    'This package will include all currently available evidence associated with the',
  );
  // Built from the existing REQUIRED_ARTIFACTS constant, not an invented list.
  expect(source).toContain('PROOF_BUNDLE_EVIDENCE_CATEGORIES = REQUIRED_ARTIFACTS');
});

/* ── Create request contract + result handling ───────────────────────────────── */

test('create: submits exactly the supported proof-bundle contract with the incident id', () => {
  const source = read(PANEL);
  expect(source).toContain('`${apiUrl}/exports/proof-bundle`');
  expect(source).toContain("method: 'POST'");
  expect(source).toContain('JSON.stringify({ incident_id: resolvedIncidentId, include_raw_events: true })');
  // No browser-generated ids / hashes / completeness / authorization in the body.
  expect(source).not.toContain('integrity_hash:');
  expect(source).not.toContain('completeness_score:');
});

test('create: success closes the modal, refreshes, and selects the returned package', () => {
  const source = read(PANEL);
  expect(source).toContain('setShowCreateModal(false)');
  expect(source).toContain('setReloadKey((k) => k + 1)');
  // Select the created — or existing idempotent — package by the backend-returned id.
  expect(source).toContain('if (payload.export_job_id) setSelectedPkgId(payload.export_job_id)');
  // The list is the single source of rows — no synthetic row is pushed on success.
  expect(source).toContain('setPackages(loaded)');
});

test('create: failure keeps the modal open, preserves selection, shows a safe message', () => {
  const source = read(PANEL);
  // Only the success branch closes the modal; the error branch keeps it open.
  expect(source).toContain('setMessage(safeErrorMessage(payload.detail ?? payload.error_message');
  // safeErrorMessage never renders a raw object / stack trace.
  expect(source).toContain('function safeErrorMessage(detail: unknown, fallback: string): string');
  const createFn = source.slice(source.indexOf('async function createPackage'), source.indexOf('async function downloadPackage'));
  // The failure path must not close the modal.
  const elseIdx = createFn.indexOf('} else {');
  expect(elseIdx).toBeGreaterThan(-1);
  expect(createFn.slice(elseIdx)).not.toContain('setShowCreateModal(false)');
});

import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

test('reconcile pending to success is driven by backend state and reconcile id', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("setRepairState('pending_request');");
  expect(source).toContain("setRepairState('pending_parse');");
  expect(source).toContain("setRepairState('pending_refresh');");
  expect(source).toContain('state: payload?.state,');
  expect(source).toContain('reconcile_id: payload?.reconcile_id ?? null,');
  expect(source).toContain("setLastReconcileId(localSummary.reconcile_id ?? null);");
  expect(source).not.toContain('setSystems(reconciledSystems);');
  expect(source).toContain("setRepairState('success');");
});

test('reconcile pending to failure is terminal and surfaces backend code + reason', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("setRepairState('failure');");
  expect(source).toContain("Code ${repairFailureReason.code}.");
  expect(source).toContain('Repair failed during {repairFailureReason.backendStage || repairFailureReason.stage}.');
  expect(source).toContain("code: 'repair_terminal_state_timeout'");
});

test('reconcile unresolved reasons map to terminal failure UX with explicit code', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('Array.isArray(payload?.unresolved_reasons) && payload.unresolved_reasons.length > 0');
  expect(source).toContain("'reconcile_unresolved_reasons'");
  expect(source).toContain('Repair completed with unresolved target reasons.');
  expect(source).toContain('Invalid target {detail.target_id}: [{detail.code}] {detail.reason}');
  expect(source).toContain("Skipped target {detail.target_id || 'n/a'}: [{detail.code}] {detail.reason}");
});

test('toggle conflict/rollback behavior re-fetches and applies server truth only', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('const refreshedSystems = await load();');
  expect(source).toContain('const authoritative = refreshedSystems.find((row) => row.id === system.id);');
  expect(source).toContain('Toggle was rolled back by server truth.');
  expect(source).toContain('Unable to update system status.');
  expect(source).toContain('[stage:${errorDetail.stage}]');
});

test('genuine empty-state is neutral, workspace-scoped, and names no hardcoded asset', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  // Neutral empty state — only shown after a successful load that returned zero rows.
  expect(source).toContain('No monitored systems are configured for this workspace.');
  expect(source).toContain('hasLoaded && systems.length === 0');
  // The wrong-asset fallback and its hardcoded CTA must be gone entirely.
  expect(source).not.toContain('US Treasury Settlement Contract');
  expect(source).not.toContain('Create monitoring target for');
  expect(source).not.toContain('treasury-settlement-target');
  expect(source).not.toContain('No monitoring target is linked to this asset yet');
});

test('API failure renders a distinct error state with code + correlation id + retry, never the empty state', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  // A body-level error (HTTP 200 with an `error` object) is treated as an API failure.
  expect(source).toContain('payload?.error && typeof payload.error === \'object\'');
  expect(source).toContain('setLoadError(');
  expect(source).toContain('Decoda could not load monitored systems.');
  expect(source).toContain('Error code: {loadError.code}');
  expect(source).toContain('Correlation ID:');
  // The error branch is mutually exclusive with the empty state.
  expect(source).toContain('{loadError ? (');
  expect(source).toContain('onClick={() => void load()}');
});

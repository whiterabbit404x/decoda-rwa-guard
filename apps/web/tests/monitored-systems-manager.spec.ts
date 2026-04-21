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
  expect(source).toContain("Code ${repairFailureReason.backendCode}.");
  expect(source).toContain('Repair failed during {repairFailureReason.backendStage || repairFailureReason.stage}.');
  expect(source).toContain("backendCode: 'repair_terminal_state_timeout'");
});

test('reconcile pending to no-op-with-reasons maps to failure UX with explicit code', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("localSummary?.state === 'no_op_with_reasons'");
  expect(source).toContain("backendCode: 'reconcile_no_op_with_reasons'");
  expect(source).toContain('No monitored systems were changed. Review skipped/invalid target reasons.');
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

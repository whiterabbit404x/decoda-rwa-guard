import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function source(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('response action controls are split by SIMULATED / RECOMMENDED / LIVE groups', () => {
  const threat = source();
  expect(threat).toContain('<span className="ruleChip">SIMULATED</span>');
  expect(threat).toContain('<span className="ruleChip">RECOMMENDED</span>');
  expect(threat).toContain('<span className="ruleChip">LIVE</span>');
  expect(threat).toContain("Freeze wallet (RECOMMENDED)");
  expect(threat).toContain("Freeze wallet (LIVE)");
  expect(threat).toContain('LIVE actions are fully separated from SIMULATED/RECOMMENDED controls');
  expect(threat).toContain('provider ID, tx hash, status, timestamps, failure reason');
});

test('live actions require explicit confirmation and linked incident context', () => {
  const threat = source();
  expect(threat).toContain('role="dialog" aria-label="Confirm live action"');
  expect(threat).toContain('LIVE action confirmation');
  expect(threat).toContain('can touch real systems');
  expect(threat).toContain('requires linked incident context');
  expect(threat).toContain('disabled={Boolean(confirmLiveActionDisabledReason)}');
  expect(threat).toContain('liveActionConfirmationText.trim().toUpperCase() !== liveActionConfirmationPhrase');
  expect(threat).toContain('Confirm LIVE action');
});

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

test('onboarding page renders top stepper and next step / resources cards', () => {
  const pageSource = readFileSync(path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'), 'utf-8');

  expect(pageSource).toContain('data-testid="onboarding-top-stepper"');
  expect(pageSource).toContain('aria-label="Onboarding steps"');
  expect(pageSource).toContain('ActionPanel title="Next Step"');
  expect(pageSource).toContain('ActionPanel title="Resources"');
  expect(pageSource).toContain('/onboarding/progress');
});

test('workflow step metadata defines visible onboarding step labels', () => {
  const stepsSource = readFileSync(path.join(__dirname, '..', 'app', 'workflow-steps.ts'), 'utf-8');

  expect(stepsSource).toContain("label: 'Workspace'");
  expect(stepsSource).toContain("label: 'Add Asset'");
  expect(stepsSource).toContain("label: 'Connect Monitoring'");
  expect(stepsSource).toContain("label: 'Enable System'");
  expect(stepsSource).toContain("label: 'First Signal'");
  expect(stepsSource).toContain("canonicalStepId: 'workspace_created'");
  expect(stepsSource).toContain("canonicalStepId: 'telemetry_received'");
});

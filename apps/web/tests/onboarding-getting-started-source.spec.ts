/**
 * Source-level contract tests for the Onboarding / Getting Started screen.
 * These verify structural requirements without needing a running server.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const clientSrc = () =>
  readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'),
    'utf-8',
  );

const stepsSrc = () =>
  readFileSync(path.join(__dirname, '..', 'app', 'workflow-steps.ts'), 'utf-8');

test('page title "Welcome to Decoda RWA Guard" is present', () => {
  expect(clientSrc()).toContain('Welcome to Decoda RWA Guard');
});

test('subtitle prompts user to complete the setup', () => {
  expect(clientSrc()).toContain('Complete the setup below to start monitoring your protected assets.');
});

test('5 setup steps are defined in ONBOARDING_TOP_STEPPER', () => {
  const src = stepsSrc();
  const labels = ['Workspace', 'Add Asset', 'Connect Monitoring', 'Enable System', 'First Signal'];
  for (const label of labels) {
    expect(src).toContain(`label: '${label}'`);
  }
});

test('stepper renders all 5 steps from ONBOARDING_TOP_STEPPER', () => {
  const src = clientSrc();
  // Component maps over topStepperSteps which is derived from ONBOARDING_TOP_STEPPER (5 items)
  expect(src).toContain('ONBOARDING_TOP_STEPPER');
  expect(src).toContain('topStepperSteps.map');
});

test('step visual states cover complete, current, and upcoming', () => {
  const src = clientSrc();
  expect(src).toContain("'complete'");
  expect(src).toContain("'current'");
  expect(src).toContain("'upcoming'");
});

test('Next Step card exists with testid', () => {
  expect(clientSrc()).toContain('data-testid="next-step-card"');
});

test('Next Step card ActionPanel title is "Next Step"', () => {
  expect(clientSrc()).toContain('ActionPanel title="Next Step"');
});

test('Resources card exists with testid', () => {
  expect(clientSrc()).toContain('data-testid="resources-card"');
});

test('Resources card contains all four resource links', () => {
  const src = clientSrc();
  expect(src).toContain('Documentation');
  expect(src).toContain('Integration Guide');
  expect(src).toContain('API Reference');
  expect(src).toContain('Help Center');
});

test('CTA button carries data-next-required-action attribute derived from backend state', () => {
  const src = clientSrc();
  // The CTA link must expose which next_required_action key drove the label
  expect(src).toContain('data-next-required-action={nextActionKey');
  // The label must be resolved from NEXT_ACTION_CTA (backend-driven map)
  expect(src).toContain('nextRequiredActionCta');
  expect(src).toContain('NEXT_ACTION_CTA');
});

test('step completion is never hardcoded — it derives from backend progress', () => {
  const src = clientSrc();
  // workflowCompletionFromState must exist and use the OnboardingProgress state
  expect(src).toContain('workflowCompletionFromState');
  expect(src).toContain('byKey.get(');
});

test('onboarding progress endpoint is called', () => {
  expect(clientSrc()).toContain('/onboarding/progress');
});

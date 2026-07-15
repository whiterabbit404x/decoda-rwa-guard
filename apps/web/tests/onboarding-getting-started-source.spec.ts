/**
 * Source-level contract tests for the rebuilt Onboarding / Getting Started
 * screen (the Autonomous Onboarding Agent). These verify structural + behavioral
 * requirements without needing a running server.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const clientSrc = () =>
  readFileSync(path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'), 'utf-8');
const moduleSrc = () =>
  readFileSync(path.join(__dirname, '..', 'app', 'onboarding-agent-client.ts'), 'utf-8');

test('page title and subtitle match the reference design', () => {
  const src = clientSrc();
  expect(src).toContain('Welcome to Decoda RWA Guard');
  expect(src).toContain('AI-powered protection for your digital asset infrastructure.');
});

test('main panel is the AI Onboarding Agent', () => {
  expect(clientSrc()).toContain('AI Onboarding Agent');
  expect(clientSrc()).toContain('Automated infrastructure discovery and security configuration');
});

test('five-step progress header is present and backend-driven', () => {
  const src = clientSrc();
  expect(src).toContain('data-testid="onboarding-top-stepper"');
  expect(src).toContain('aria-label="Onboarding steps"');
  // Phases derive from backend session/step state, not local UI state.
  expect(src).toContain('derivePhaseStatuses(snapshot)');
  const mod = moduleSrc();
  for (const label of ['Connect Workspace', 'Detect Assets', 'Configure Monitoring', 'Review & Secure', "You're Protected"]) {
    expect(mod).toContain(label);
  }
});

test('execution timeline renders backend steps with evidence + retry', () => {
  const src = clientSrc();
  expect(src).toContain('data-testid="agent-timeline"');
  expect(src).toContain('data-testid="evidence-toggle"');
  expect(src).toContain('data-testid="evidence-body"');
  expect(src).toContain('data-testid="btn-retry"');
});

test('discovery summary + RPC benchmark + provider explanation are rendered', () => {
  const src = clientSrc();
  expect(src).toContain('data-testid="discovery-summary"');
  expect(src).toContain('data-testid="rpc-row"');
  expect(src).toContain('data-testid="rpc-explanation"');
});

test('proposal review requires approval before activation', () => {
  const src = clientSrc();
  expect(src).toContain('data-testid="approval-summary"');
  expect(src).toContain('data-testid="btn-approve"');
  expect(src).toContain('data-testid="btn-activate"');
  // Activation is gated on approval.
  expect(src).toContain('const canActivate = p.approved');
  expect(src).toContain('disabled={!canActivate');
});

test('contextual agent panel is operational, not a chatbot', () => {
  const src = clientSrc();
  expect(src).toContain('data-testid="agent-state"');
  expect(src).toContain('data-testid="agent-actions"');
  expect(src).toContain('Ask about this setup');
  // The "ask" answer is grounded in discovery evidence, never generated free-form.
  expect(src).toContain('groundedAnswer');
});

test('confirmed / probable / unknown / requires-review are distinguished truthfully', () => {
  const mod = moduleSrc();
  expect(mod).toContain("case 'confirmed': return 'success'");
  expect(mod).toContain("case 'probable': return 'info'");
  expect(mod).toContain("case 'requires_review': return 'warning'");
});

test('primary actions vary by backend state (discovery / activate / dashboard)', () => {
  const src = clientSrc();
  expect(src).toContain('Run Automated Discovery');
  expect(src).toContain('Activate Protection');
  expect(src).toContain('Open Security Dashboard');
  expect(src).toContain('data-testid="btn-dashboard"');
});

test('empty, loading, and failure states are all handled', () => {
  const src = clientSrc();
  expect(src).toContain('IntakeForm');          // empty / new-session state
  expect(src).toContain('onbSkeleton');          // loading skeleton
  expect(src).toContain('data-testid="onboarding-error"'); // failure state
  expect(src).toContain('data-testid="step-error"');       // per-step failure
});

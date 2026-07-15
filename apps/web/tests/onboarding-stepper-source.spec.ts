/**
 * Source-level contracts for the Onboarding Agent stepper + live-progress wiring.
 * Progress must come from persisted backend state + SSE, never simulated timers.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const clientSrc = () =>
  readFileSync(path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'), 'utf-8');
const moduleSrc = () =>
  readFileSync(path.join(__dirname, '..', 'app', 'onboarding-agent-client.ts'), 'utf-8');

test('stepper + timeline render from the backend snapshot', () => {
  const src = clientSrc();
  expect(src).toContain('data-testid="onboarding-top-stepper"');
  expect(src).toContain('phases.map');
  expect(src).toContain('snapshot.steps.map');
});

test('progress is never simulated with timers — polling refetches real backend state', () => {
  const src = clientSrc();
  // The only interval refetches the authoritative session; it never fabricates step state.
  expect(src).toContain('void refresh(sessionId)');
  expect(src).toContain('/api/onboarding/sessions/${sessionId}');
  expect(src).not.toContain('setTimeout(() => setSnapshot');
});

test('live SSE events trigger an authoritative refetch (polling only as fallback)', () => {
  const src = clientSrc();
  expect(src).toContain('connectOnboardingStream');
  expect(src).toContain('onEvent: () => { void refresh(sessionId); }');
  expect(src).toContain("streamStatus === 'live'"); // polling suppressed while SSE is live
});

test('session is restored after a browser refresh', () => {
  const src = clientSrc();
  expect(src).toContain('window.localStorage.getItem(STORAGE_KEY)');
  expect(src).toContain('/api/onboarding/sessions/${stored}');
});

test('duplicate submissions are prevented while a request is in flight', () => {
  const src = clientSrc();
  expect(src).toContain('if (busy) return;'); // guard at the top of run()
  expect(src).toContain('disabled={busy !== null}');
});

test('SSE client parses events and exposes live/polling/disconnected status', () => {
  const mod = moduleSrc();
  expect(mod).toContain('export function connectOnboardingStream');
  expect(mod).toContain("callbacks.onStatus('live')");
  expect(mod).toContain("callbacks.onStatus('polling')");
  // Reconnect loop, not a one-shot connection.
  expect(mod).toContain('while (!closed)');
});

test('responsive layout uses a scrollable table container', () => {
  expect(clientSrc()).toContain('onbTableScroll');
});

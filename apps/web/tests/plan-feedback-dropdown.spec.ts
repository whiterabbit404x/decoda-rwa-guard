/**
 * Contract tests: Pilot "Give feedback" → Feedback type dropdown.
 *
 * The plan panel's Feedback type control used a native <select>. Chrome draws
 * the native option popup with the OS and composites the control's background
 * over white, so `.planFeedbackInput`'s translucent `rgba(255,255,255,0.04)`
 * surface rendered as a white popup while the option text kept the dark
 * theme's light `--text-primary` — unreadable for every row except the one the
 * OS painted with its own highlight. `color-scheme: dark` (already pinned at
 * :root) does not override an author-set control background, and Windows
 * Chrome ignores author `option` colours in the OS popup, so these tests lock
 * in the same fix the onboarding dropdowns already use: the shared, in-app
 * Select listbox, themed from semantic tokens, with the backend payload and
 * the feedback vocabulary unchanged.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const badgeSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'plan-badge.tsx'), 'utf-8');
const feedbackSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'plan-feedback.ts'), 'utf-8');
const stylesSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');

function block(src: string, startNeedle: string): string {
  const start = src.indexOf(startNeedle);
  if (start < 0) return '';
  const braceStart = src.indexOf('{', start);
  const end = src.indexOf('}', braceStart);
  return src.slice(start, end < 0 ? undefined : end + 1);
}

test.describe('Feedback type no longer uses a native <select> popup', () => {
  test('the plan panel imports and uses the shared Select primitive', () => {
    expect(badgeSrc).toMatch(/import\s*{[^}]*\bSelect\b[^}]*}\s*from\s*'\.\/components\/ui-primitives'/);
    expect(badgeSrc).toContain('<Select');
  });

  test('no native <select> / <option> remains in the plan badge', () => {
    expect(badgeSrc).not.toContain('<select');
    expect(badgeSrc).not.toContain('<option');
  });

  test('the control keeps its id and gains a stable test id', () => {
    expect(badgeSrc).toContain('id="plan-feedback-type"');
    expect(badgeSrc).toContain('testId="plan-feedback-type"');
  });
});

test.describe('backend payload and feedback vocabulary are preserved', () => {
  test('options still come from the shared vocabulary, value and label intact', () => {
    expect(badgeSrc).toContain(
      'options={FEEDBACK_TYPE_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}',
    );
    expect(badgeSrc).toContain('value={feedbackType}');
    expect(badgeSrc).toContain('onValueChange={(value) => setFeedbackType(value)}');
  });

  test('the submitted body still posts the raw feedback_type string', () => {
    expect(badgeSrc).toContain('feedback_type: feedbackType');
    expect(badgeSrc).toContain("useState<string>('usability')");
  });

  test('every backend-accepted feedback type is still offered', () => {
    for (const value of [
      'security',
      'detection_accuracy',
      'usability',
      'missing_feature',
      'integration',
      'other',
    ]) {
      expect(feedbackSrc).toContain(`value: '${value}'`);
    }
    // Labels named in the bug report must remain selectable.
    for (const label of ['Detection accuracy', 'Usability', 'Missing feature', 'Integration', 'Other']) {
      expect(feedbackSrc).toContain(`label: '${label}'`);
    }
  });
});

test.describe('the control stays accessible', () => {
  test('the visible label both points at and names the trigger', () => {
    expect(badgeSrc).toContain('id="plan-feedback-type-label"');
    expect(badgeSrc).toContain('htmlFor="plan-feedback-type"');
    expect(badgeSrc).toContain('ariaLabelledBy="plan-feedback-type-label"');
  });

  test('the message field keeps its own label association', () => {
    expect(badgeSrc).toContain('htmlFor="plan-feedback-message"');
    expect(badgeSrc).toContain('id="plan-feedback-message"');
  });
});

test.describe('the opened menu is themed, not a white popup', () => {
  test('menu surface and text come from semantic theme tokens', () => {
    const menu = block(stylesSrc, '.dcSelectMenu {');
    expect(menu).toContain('background: var(--popover)');
    expect(menu).toContain('color: var(--popover-fg)');
  });

  test('hover / selected / disabled / focus states are all defined', () => {
    expect(stylesSrc).toContain('.dcSelectOption[data-active]');
    expect(stylesSrc).toContain('.dcSelectOption[data-selected]');
    expect(stylesSrc).toContain('.dcSelectOption[data-disabled]');
    expect(stylesSrc).toContain('.dcSelectTrigger:focus-visible');
  });

  test('the compact override tunes metrics only — no colours are overridden', () => {
    const compact = block(stylesSrc, '.planFeedbackSelect .dcSelectTrigger {');
    expect(compact).toContain('padding: 0.4rem 0.5rem');
    expect(compact).toContain('font-size: 0.8rem');
    expect(compact).not.toMatch(/background|color\s*:/);
  });

  test('the translucent input surface no longer backs an OS-drawn popup', () => {
    // The textarea may keep it — it has no native popup — but the Feedback type
    // control must not be a native <select> carrying that background.
    const input = block(stylesSrc, '.planFeedbackInput {');
    expect(input).toContain('background: rgba(255, 255, 255, 0.04)');
    expect(badgeSrc).not.toMatch(/<select[^>]*className="planFeedbackInput"/);
  });
});

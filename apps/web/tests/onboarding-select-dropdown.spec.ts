/**
 * Contract tests: onboarding Network / Monitoring-mode dropdowns.
 *
 * The onboarding intake form previously used native <select> elements, whose
 * opened option list is rendered by the OS as a bright white popup on
 * Chrome/Windows regardless of the dark theme. These tests lock in the fix:
 *   - the native <select>s are replaced by the shared, in-app Select listbox,
 *   - the shared Select is accessible (roles, keyboard, portal),
 *   - the opened menu is themed from semantic tokens (no white popup),
 *   - the submitted backend payload is unchanged (numeric chain id / mode enum),
 *   - a color-scheme fallback covers any remaining native controls.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const clientSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'),
  'utf-8',
);
const primitivesSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'components', 'ui-primitives.tsx'),
  'utf-8',
);
const stylesSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');
const layoutSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'layout.tsx'), 'utf-8');

function block(src: string, startNeedle: string): string {
  const start = src.indexOf(startNeedle);
  if (start < 0) return '';
  const braceStart = src.indexOf('{', start);
  const end = src.indexOf('}', braceStart);
  return src.slice(start, end < 0 ? undefined : end + 1);
}

test.describe('onboarding no longer uses native <select> popups', () => {
  test('the intake form imports and uses the shared Select primitive', () => {
    expect(clientSrc).toMatch(/import\s*{[^}]*\bSelect\b[^}]*}\s*from\s*'\.\.\/components\/ui-primitives'/);
    expect(clientSrc).toContain('<Select');
  });

  test('no native <select> / <option> remain in the onboarding client', () => {
    expect(clientSrc).not.toContain('<select');
    expect(clientSrc).not.toContain('<option');
  });

  test('test ids are preserved for both dropdowns', () => {
    expect(clientSrc).toContain('testId="input-chain"');
    expect(clientSrc).toContain('testId="input-mode"');
  });
});

test.describe('backend payload values are preserved', () => {
  test('Network submits the numeric chain id (Number conversion kept)', () => {
    expect(clientSrc).toContain('chain_id: Number(v)');
    // options carry the chain id as the option value
    expect(clientSrc).toContain('options={CHAINS.map((c) => ({ value: String(c.id), label: c.label }))}');
    // controlled value round-trips the numeric chain id as a string
    expect(clientSrc).toContain('value={String(form.chain_id)}');
  });

  test('Base Mainnet = 8453 and Ethereum Mainnet = 1 are still defined', () => {
    expect(clientSrc).toMatch(/id:\s*8453,\s*label:\s*'Base Mainnet \(8453\)'/);
    expect(clientSrc).toMatch(/id:\s*1,\s*label:\s*'Ethereum Mainnet \(1\)'/);
  });

  test('Monitoring mode still submits its enum value unchanged', () => {
    expect(clientSrc).toContain('monitoring_mode: v');
    expect(clientSrc).toContain("id: 'recommended'");
    expect(clientSrc).toContain("id: 'strict'");
    expect(clientSrc).toContain("id: 'custom'");
    // onboarding request still posts monitoring_mode from form state
    expect(clientSrc).toContain('monitoring_mode: form.monitoring_mode');
  });
});

test.describe('shared Select is accessible', () => {
  test('exposes combobox/listbox/option roles and popup semantics', () => {
    expect(primitivesSrc).toContain("role=\"combobox\"");
    expect(primitivesSrc).toContain("aria-haspopup=\"listbox\"");
    expect(primitivesSrc).toContain("role=\"listbox\"");
    expect(primitivesSrc).toContain("role=\"option\"");
    expect(primitivesSrc).toContain('aria-expanded={open}');
    expect(primitivesSrc).toContain('aria-selected={isSelected}');
    expect(primitivesSrc).toContain('aria-activedescendant');
  });

  test('reflects disabled, required and error states to assistive tech', () => {
    expect(primitivesSrc).toContain('aria-required={required || undefined}');
    expect(primitivesSrc).toContain('aria-invalid={error || undefined}');
    expect(primitivesSrc).toContain('disabled={disabled}');
  });

  test('supports the full keyboard model', () => {
    for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', 'Escape', 'Tab']) {
      expect(primitivesSrc).toContain(`'${key}'`);
    }
    // Space (both spellings) opens/selects
    expect(primitivesSrc).toContain("case ' '");
  });

  test('renders the menu in-app via a portal (client-only, no hydration mismatch)', () => {
    expect(primitivesSrc).toContain("import { createPortal } from 'react-dom'");
    expect(primitivesSrc).toContain('createPortal(');
    expect(primitivesSrc).toContain('document.body');
    // portal is gated on a mounted flag so nothing is server-rendered
    expect(primitivesSrc).toContain('open && mounted && pos');
  });

  test('an associated label can be provided (no orphan control)', () => {
    expect(clientSrc).toContain('ariaLabelledBy="onb-field-network"');
    expect(clientSrc).toContain('ariaLabelledBy="onb-field-mode"');
    expect(clientSrc).toContain('id="onb-field-network"');
    expect(clientSrc).toContain('id="onb-field-mode"');
  });
});

test.describe('opened menu is themed (no white popup) via semantic tokens', () => {
  test('menu surface, text and border come from theme variables', () => {
    const menu = block(stylesSrc, '.dcSelectMenu {');
    expect(menu).toContain('background: var(--popover)');
    expect(menu).toContain('color: var(--popover-fg)');
    expect(menu).toContain('var(--border-accent)');
    expect(menu).toContain('z-index: 1000');
    expect(menu).toContain('overflow-y: auto');
  });

  test('the Select CSS hard-codes no white background', () => {
    const start = stylesSrc.indexOf('.dcSelect {');
    const end = stylesSrc.indexOf('@media (prefers-reduced-motion: reduce) {\n  .dcSelectArrow');
    const selectCss = stylesSrc.slice(start, end < 0 ? start + 4000 : end);
    expect(selectCss).not.toMatch(/background:\s*(#fff\b|#ffffff\b|white\b)/i);
  });

  test('semantic popover / ring tokens exist and alias existing theme tokens', () => {
    expect(stylesSrc).toContain('--popover:     var(--bg-surface)');
    expect(stylesSrc).toContain('--popover-fg:  var(--text-primary)');
    expect(stylesSrc).toContain('--ring:        var(--accent-blue)');
  });

  test('hover / selected / active states are defined', () => {
    expect(stylesSrc).toContain('.dcSelectOption[data-active]');
    expect(stylesSrc).toContain('.dcSelectOption[data-selected]');
    expect(stylesSrc).toContain('.dcSelectTrigger:focus-visible');
  });
});

test.describe('color-scheme fallback for remaining native controls', () => {
  test(':root advertises both schemes and theme attributes pin one', () => {
    expect(stylesSrc).toContain('color-scheme: light dark');
    expect(stylesSrc).toMatch(/\[data-theme="dark"\],\s*\.dark\s*{\s*color-scheme: dark;/);
    expect(stylesSrc).toMatch(/\[data-theme="light"\],\s*\.light\s*{\s*color-scheme: light;/);
  });

  test('the document is pinned to the dark theme by default', () => {
    expect(layoutSrc).toContain('data-theme="dark"');
  });
});

/**
 * A whole-page WCAG contrast sweep, for use against the render harness.
 *
 * Enumerating selectors by hand only ever finds the problems you thought to
 * look for. This walks EVERY rendered text node on a mounted screen, resolves
 * the colour actually painted behind it (compositing translucent ancestors),
 * and reports anything that fails AA. A token that flips the wrong way in one
 * theme shows up here without anyone having predicted which element it would
 * strand.
 *
 * Runs entirely in the page, so it costs one `evaluate` per theme rather than
 * one round-trip per element.
 */
import { expect, type Page } from '@playwright/test';

export type ContrastFailure = {
  selector: string;
  text: string;
  colour: string;
  background: string;
  ratio: number;
  required: number;
};

/** Apply a theme the way the product does: one attribute on <html>. */
export async function applyTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.evaluate((value) => {
    document.documentElement.setAttribute('data-theme', value);
  }, theme);
  // Let colour transitions settle so readings are of the resting state, not
  // of a frame mid-animation.
  await page.waitForTimeout(200);
}

export async function sweepContrast(
  page: Page,
  options: { ignore?: string[] } = {},
): Promise<ContrastFailure[]> {
  return page.evaluate((ignoreSelectors: string[]) => {
    const channel = (v: number) => {
      const s = v / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    const luminance = (rgb: number[]) =>
      0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
    const parse = (value: string): number[] | null => {
      const parts = value.match(/[\d.]+/g);
      return parts ? [Number(parts[0]), Number(parts[1]), Number(parts[2]), parts[3] === undefined ? 1 : Number(parts[3])] : null;
    };

    /** The opaque colour actually behind an element. */
    const paintedBehind = (start: HTMLElement): number[] => {
      const layers: number[][] = [];
      let node: HTMLElement | null = start;
      while (node) {
        const parsed = parse(getComputedStyle(node).backgroundColor);
        if (parsed && parsed[3] > 0) {
          layers.push(parsed);
          if (parsed[3] === 1) break;
        }
        node = node.parentElement;
      }
      let base = [255, 255, 255];
      for (let i = layers.length - 1; i >= 0; i--) {
        const [r, g, b, a] = layers[i];
        base = [a * r + (1 - a) * base[0], a * g + (1 - a) * base[1], a * b + (1 - a) * base[2]];
      }
      return base;
    };

    const describe = (el: HTMLElement): string => {
      const id = el.id ? `#${el.id}` : '';
      const cls = typeof el.className === 'string' && el.className
        ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}`
        : '';
      return `${el.tagName.toLowerCase()}${id}${cls}`;
    };

    const failures: Array<Record<string, unknown>> = [];
    const ignored = ignoreSelectors.length
      ? new Set(Array.from(document.querySelectorAll(ignoreSelectors.join(','))))
      : new Set<Element>();

    document.querySelectorAll<HTMLElement>('body *').forEach((el) => {
      if (ignored.has(el)) return;
      for (const ancestor of ignored) if (ancestor.contains(el)) return;

      // Only elements that paint their OWN text: an element whose text lives
      // in a child is measured at the child instead, where the colour applies.
      const ownText = Array.from(el.childNodes)
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => (n.textContent ?? '').trim())
        .join(' ')
        .trim();
      if (!ownText) return;

      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') return;
      if (Number(style.opacity) === 0) return;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;

      // WCAG 1.4.3 exempts inactive controls: a disabled button is SUPPOSED
      // to read as unavailable, and dimming is how it says so. The shell spec
      // still holds disabled controls to a legibility floor separately, so
      // this exemption cannot hide a control that has faded to nothing.
      const disabled = el.closest('[disabled], [aria-disabled="true"], fieldset[disabled]');
      if (disabled) return;

      const colour = parse(style.color);
      if (!colour) return;
      const bg = paintedBehind(el);

      // Composite the ink's own alpha and any inherited element opacity.
      const elementOpacity = Number(style.opacity);
      const inkAlpha = colour[3] * (Number.isFinite(elementOpacity) ? elementOpacity : 1);
      const ink = [0, 1, 2].map((i) => inkAlpha * colour[i] + (1 - inkAlpha) * bg[i]);

      const a = luminance(ink);
      const b = luminance(bg);
      const [hi, lo] = a > b ? [a, b] : [b, a];
      const ratio = (hi + 0.05) / (lo + 0.05);

      // WCAG large text: >= 18.66px bold, or >= 24px.
      const size = parseFloat(style.fontSize);
      const weight = Number(style.fontWeight) || 400;
      const isLarge = size >= 24 || (size >= 18.66 && weight >= 700);
      const required = isLarge ? 3 : 4.5;

      if (ratio < required) {
        failures.push({
          selector: describe(el),
          text: ownText.slice(0, 48),
          colour: style.color,
          background: `rgb(${bg.map(Math.round).join(',')})`,
          ratio: Math.round(ratio * 100) / 100,
          required,
        });
      }
    });

    return failures;
  }, options.ignore ?? []) as Promise<ContrastFailure[]>;
}

/** Sweep a mounted page in both themes and fail with a readable report. */
export async function expectReadableInBothThemes(
  page: Page,
  label: string,
  options: { ignore?: string[] } = {},
): Promise<void> {
  for (const theme of ['light', 'dark'] as const) {
    await applyTheme(page, theme);
    const failures = await sweepContrast(page, options);
    const report = failures
      .map((f) => `  ${f.selector}  ${f.ratio}:1 (needs ${f.required}) — "${f.text}" ${f.colour} on ${f.background}`)
      .join('\n');
    expect(failures, `${label} — ${theme} theme contrast failures:\n${report}`).toEqual([]);
  }
}

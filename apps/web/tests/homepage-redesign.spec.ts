import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Source-level guardrails for the institutional redesign of the public landing
 * page. Like the sibling homepage specs these run without a web server, so they
 * stay reliable in CI.
 *
 * What they pin:
 *   • the dark-navy / light-enterprise zoning, and the tokens behind it
 *   • the motion budget — one looping animation, in the hero, and nothing else
 *   • reduced motion landing on the completed state rather than a blank one
 *   • the CSP constraint that forbids inline styles on this page
 *   • no invented customers, certifications or production metrics
 */

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

function componentFiles(): { name: string; source: string }[] {
  return fs
    .readdirSync(HOME_DIR)
    .filter((file) => file.endsWith('.tsx'))
    .map((file) => ({ name: file, source: read(HOME_DIR, file) }));
}

const css = () => read(HOME_DIR, 'home.module.css');

/**
 * The brace-balanced body of the block whose header matches `header`.
 *
 * `\{[^}]*(\{[^}]*\}[^}]*)*\}` looks like it reads a nested block and does
 * not: `[^}]` admits `{`, so the first `}` in the source ends the match and a
 * six-step @keyframes comes back as its first step. Counting braces is the
 * only way to assert anything about a whole keyframe set.
 */
function blockBody(source: string, header: RegExp): string {
  const match = header.exec(source);
  if (!match) return '';
  const open = source.indexOf('{', match.index);
  if (open === -1) return '';
  let depth = 1;
  let end = open + 1;
  while (end < source.length && depth > 0) {
    if (source[end] === '{') depth += 1;
    else if (source[end] === '}') depth -= 1;
    end += 1;
  }
  return source.slice(open + 1, end - 1);
}

/** Every @keyframes body in the stylesheet, whole. */
function keyframeBodies(source: string): { name: string; body: string }[] {
  return [...source.matchAll(/@keyframes\s+([\w-]+)/g)].map((match) => ({
    name: match[1],
    body: blockBody(source, new RegExp(`@keyframes\\s+${match[1]}\\b`)),
  }));
}

/**
 * Style rules from a stylesheet, comments stripped, @keyframes dropped and
 * @media recursed into. Brace-balanced, so a one-line keyframe block cannot
 * leak its percentage steps in as if they were selectors.
 */
function styleRules(source: string): { selector: string; body: string }[] {
  const clean = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const rules: { selector: string; body: string }[] = [];
  let cursor = 0;
  while (cursor < clean.length) {
    const open = clean.indexOf('{', cursor);
    if (open === -1) break;
    const selector = clean.slice(cursor, open).trim();
    let depth = 1;
    let end = open + 1;
    while (end < clean.length && depth > 0) {
      if (clean[end] === '{') depth += 1;
      else if (clean[end] === '}') depth -= 1;
      end += 1;
    }
    const body = clean.slice(open + 1, end - 1);
    if (selector.startsWith('@media')) {
      rules.push(...styleRules(body));
    } else if (!selector.startsWith('@')) {
      rules.push({ selector, body });
    }
    cursor = end;
  }
  return rules;
}

// ── 1. Dark / light zoning ────────────────────────────────────
test.describe('landing theme zoning', () => {
  test('the navy and light-enterprise tokens are defined once, on the page root', () => {
    const source = css();
    for (const token of [
      '--navy-950: #07111f',
      '--navy-900: #0b1220',
      '--navy-850: #101a2b',
      '--bg: #f7f9fc',
      '--surface: #ffffff',
      '--surface-soft: #f1f5f9',
      '--border: #e2e8f0',
      '--text: #0f172a',
      '--text-2: #475569',
      '--text-3: #64748b',
    ]) {
      expect(source, `expected landing token "${token}"`).toContain(token);
    }
  });

  test('the dark zones are the navbar, hero, final CTA and footer — and nothing else', () => {
    const darkZoned = componentFiles()
      .filter((file) => file.source.includes('styles.zoneDark'))
      .map((file) => file.name)
      .sort();
    expect(darkZoned).toEqual([
      'final-cta.tsx',
      'hero-section.tsx',
      'marketing-footer.tsx',
      'marketing-header.tsx',
    ]);
  });

  test('the body of the page is the light enterprise surface', () => {
    const source = css();
    expect(source).toContain('.page {');
    // The page root paints the light background; sections inherit it.
    expect(source).toMatch(/\.page\s*\{[^}]*background:\s*var\(--bg\)/s);
  });

  test('no dark surface is pure black', () => {
    const source = css();
    expect(source).not.toMatch(/#000\b/);
    expect(source).not.toContain('#000000');
    expect(source).not.toMatch(/\brgb\(\s*0\s*,\s*0\s*,\s*0\s*\)/);
  });
});

// ── 2. Motion budget ──────────────────────────────────────────
test.describe('animation budget', () => {
  test('the hero incident workflow is the only looping animation on the page', () => {
    const infinite = css()
      .split('\n')
      .filter((line) => line.includes('infinite'));
    expect(infinite.length).toBe(3);
    for (const line of infinite) {
      expect(line, `unexpected looping animation: ${line.trim()}`).toMatch(/wfStage1|wfRailAdvance|wfGlow1/);
    }
  });

  test('the workflow loop runs a slow, enterprise-paced cycle', () => {
    const source = css();
    // Six stages ~1s apart, a ~1.8s settled hold on the proven end state,
    // then a soft reset — 8.4s end to end.
    expect(source).toContain('animation: wfStage1 8.4s linear infinite');
    expect(source).toContain('animation: wfRailAdvance 8.4s linear infinite');
    expect(source).toContain('animation: wfGlow1 8.4s linear infinite');
  });

  test('the workflow loop has three readable states, so exactly one step is current', () => {
    const source = css();
    // The bug this pins: a row used to go to full opacity and STAY there, so
    // after the first pass nothing on the panel was moving and the hero read
    // as a static list. Each stage must fall back to a "completed" level when
    // the next one takes over.
    for (let stage = 1; stage <= 6; stage += 1) {
      const block = blockBody(source, new RegExp(`@keyframes wfStage${stage}\\b`));
      expect(block, `wfStage${stage} must exist`).not.toBe('');
      const levels = [...block.matchAll(/opacity:\s*([\d.]+)/g)].map((m) => Number(m[1]));
      expect(new Set(levels).size, `wfStage${stage} needs inactive/active/completed`)
        .toBeGreaterThanOrEqual(stage === 6 ? 2 : 3);
      expect(Math.max(...levels)).toBe(1);
      expect(Math.min(...levels)).toBeLessThan(0.5);
    }
    // The completed level sits between inactive and active on every stage.
    expect(source).toContain('opacity: 0.7;');
    expect(source).toContain('opacity: 0.34;');
  });

  test('no animation or transition on the page touches a layout property', () => {
    const source = css();
    const keyframeBlocks = keyframeBodies(source);
    expect(keyframeBlocks.length).toBeGreaterThan(0);
    for (const { name, body } of keyframeBlocks) {
      expect(body, `@keyframes ${name} came back empty`).not.toBe('');
      for (const banned of ['width:', 'height:', 'top:', 'left:', 'margin', 'padding']) {
        expect(body, `@keyframes ${name} animates layout property "${banned}"`).not.toContain(banned);
      }
      // and only the two properties the compositor can animate on its own
      for (const property of [...body.matchAll(/([a-z-]+):/g)].map((m) => m[1])) {
        expect(property, `@keyframes ${name} animates "${property}"`).toMatch(/^(opacity|transform)$/);
      }
    }
    // Reveal transitions may only move opacity, transform and paint-only
    // properties, so an entrance can never shift the layout around it.
    // (Activating a lifecycle stage recolours its ring; that repaints, it does
    // not reflow.)
    const revealTransitions = [...source.matchAll(/transition:\s*([^;]*var\(--ease-reveal\)[^;]*);/g)]
      .map((match) => match[1]);
    expect(revealTransitions.length).toBeGreaterThan(0);
    for (const declaration of revealTransitions) {
      for (const property of declaration.split(',')) {
        expect(property.trim(), `reveal transitions may not reflow: ${declaration}`)
          .toMatch(/^(opacity|transform|color|background-color|border-color|box-shadow)\s/);
      }
    }
  });

  test('pricing carries no entrance animation at all', () => {
    const pricing = read(HOME_DIR, 'pricing-section.tsx');
    expect(pricing).not.toContain('ScrollReveal');
    expect(pricing).not.toContain('reveal');
  });

  test('no animation dependency was added to the web app', () => {
    const pkg = JSON.parse(read(APP_DIR, '..', 'package.json')) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    const all = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    for (const banned of ['framer-motion', 'motion', 'gsap', 'react-spring', '@react-spring/web', 'lottie-react', 'aos']) {
      expect(all, `unexpected animation dependency "${banned}"`).not.toContain(banned);
    }
  });
});

// ── 3. Reduced motion ─────────────────────────────────────────
test.describe('reduced motion', () => {
  test('reduced motion switches every animation and transition off', () => {
    const source = css();
    expect(source).toContain('@media (prefers-reduced-motion: reduce)');
    const block = source.slice(source.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(block).toContain('animation: none !important');
    expect(block).toContain('transition: none !important');
  });

  test('reduced motion resolves to the COMPLETED workflow, not a blank one', () => {
    const source = css();
    // The static rule set is the finished state, so switching the animation off
    // is what reveals it. The rail is drawn, and the stages carry their tone.
    expect(source).toMatch(/\.wfRailFill\s*\{[^}]*transform:\s*scaleY\(1\)/s);
    expect(blockBody(source, /@keyframes wfRailAdvance\b/))
      .toMatch(/^\s*0%,\s*5%\s*\{\s*transform:\s*scaleY\(0\)/);
    // Each stage's keyframes start dimmed and settle at full opacity.
    expect(source).toMatch(/@keyframes wfStage1\s*\{[^}]*opacity:\s*0\.34/s);
  });

  test('reduced motion restores every revealed element to visible', () => {
    const block = css().slice(css().indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(block).toContain('opacity: 1 !important');
    expect(block).toContain('transform: none !important');
    expect(block).toContain('scaleX(1) !important');
  });

  test('the workflow needs no JavaScript to render or to animate', () => {
    const demo = read(HOME_DIR, 'incident-workflow-demo.tsx');
    expect(demo).not.toContain("'use client'");
    expect(demo).not.toContain('useEffect');
    expect(demo).not.toContain('useState');
    const hero = read(HOME_DIR, 'hero-section.tsx');
    expect(hero).not.toContain("'use client'");
  });
});

// ── 4. Scroll reveal safety ───────────────────────────────────
test.describe('scroll reveal', () => {
  test('content is readable without JavaScript — every reveal rule is keyed on data-reveal', () => {
    const source = css();
    // Nothing on the page may hide itself unconditionally: every rule that
    // sets `opacity: 0` is either keyed on the attribute scroll-reveal.tsx
    // sets, or is a decorative ::before/::after overlay that carries no
    // content. So a visitor whose JS never runs reads the finished page.
    const hidingRules = styleRules(source)
      .filter((rule) => /opacity:\s*0\s*;/.test(rule.body))
      .map((rule) => rule.selector);
    expect(hidingRules.length).toBeGreaterThan(0);
    for (const selector of hidingRules) {
      const keyed = selector.includes('data-reveal');
      const decorative = selector.split(',').every((part) => /::(after|before)\b/.test(part));
      expect(keyed || decorative, `rule hides content without JavaScript: ${selector}`).toBe(true);
    }
    expect(source).toContain(".reveal[data-reveal='out']:not(.revealParts) {");
    // The old document-wide flag is gone: it hid content after first paint and
    // was the reason the entrance never actually played.
    expect(source).not.toContain('data-landing-reveal');
  });

  test('the transition lives on the revealed rule, so applying the hidden state cannot animate', () => {
    const source = css();
    const out = source.slice(source.indexOf(".reveal[data-reveal='out']"));
    const outRule = out.slice(0, out.indexOf('}'));
    expect(outRule, 'the hidden rule must not declare a transition').not.toContain('transition');
    expect(source).toMatch(/\.reveal\[data-reveal='in'\]:not\(\.revealParts\)\s*\{[^}]*transition:/s);
  });

  test('the reveal matches the specified distance, duration and easing', () => {
    const source = css();
    expect(source).toContain('--ease-reveal: cubic-bezier(0.22, 1, 0.36, 1)');
    // A whole block travels 36px; staggered children (headings, card grids)
    // travel 32px. Under 24px the entrance is not noticeable at reading size,
    // which is what made the previous 20px reveal read as "nothing happened".
    expect(source).toMatch(/\.reveal\[data-reveal='out'\]:not\(\.revealParts\)\s*\{[^}]*translateY\(36px\)/s);
    expect(source).toMatch(/\.revealStagger\[data-reveal='out'\] > \*\s*\{[^}]*translateY\(32px\)/s);
    const distances = [...source.matchAll(/transform:\s*translateY\((\d+)px\)/g)].map((m) => Number(m[1]));
    expect(distances.length).toBeGreaterThan(0);
    for (const distance of distances) {
      expect(distance, `${distance}px is too small to be noticed`).toBeGreaterThanOrEqual(24);
      expect(distance, `${distance}px is far enough to feel like a slide`).toBeLessThanOrEqual(40);
    }
    // Every reveal duration sits inside the 440–750ms budget.
    const durations = [...source.matchAll(/transition:\s*opacity\s+([\d.]+)s\s+var\(--ease-reveal\)/g)]
      .map((match) => Number(match[1]) * 1000);
    expect(durations.length).toBeGreaterThan(0);
    for (const duration of durations) {
      expect(duration, `reveal duration ${duration}ms is outside 440–750ms`).toBeGreaterThanOrEqual(440);
      expect(duration, `reveal duration ${duration}ms is outside 440–750ms`).toBeLessThanOrEqual(750);
    }
  });

  test('the trigger is measured in screen pixels, not as a percent of the element', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    // THE regression this file exists for. A bare percent-of-the-element
    // threshold is the right question for a 150px heading and the wrong one
    // for an 800px section: 18–22% of a tall block is its top sliver, so the
    // block "arrived" while two thirds of it were still below the fold and the
    // whole sequence finished before the reader ever saw it.
    expect(reveal).toContain('const REVEAL_RATIO = 0.22');
    expect(reveal).toContain('const READING_INSET = 0.12');
    expect(reveal).toContain('const MIN_READING_PX = 150');
    expect(reveal).toContain('const MAX_READING_SHARE = 0.42');
    // The floor is what stops a tall block triggering on its top edge, and the
    // cap is what stops a very tall one waiting for an impossible amount of
    // screen. Both are in pixels, both are applied in requiredVisiblePx().
    expect(reveal).toMatch(/Math\.max\(blockHeight \* REVEAL_RATIO, MIN_READING_PX\)/);
    expect(reveal).toMatch(/Math\.min\(wanted, blockHeight, viewport \* MAX_READING_SHARE\)/);
    expect(reveal).toContain('rootMargin: `0px 0px -${READING_INSET * 100}% 0px`');
  });

  test('the load-time check and the scroll trigger share ONE predicate', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    // Two copies of the rule is how a section ends up staged by one and
    // written off as already-read by the other.
    expect(reveal).toContain('function arrived(');
    expect((reveal.match(/function arrived\(/g) ?? []).length).toBe(1);
    // the observer path
    expect(reveal).toMatch(/arrived\(entry\.boundingClientRect, entry\.intersectionRect\.height\)/);
    // the load-time path
    expect(reveal).toContain('getBoundingClientRect');
    expect(reveal).toMatch(/return arrived\(rect, visibleHeight\(rect\)\)/);
    expect(reveal).toContain('if (hasArrived(el))');
    // A threshold cannot express "150px of whatever this block is", so the
    // thresholds must only wake the callback, never decide on their own.
    expect(reveal).toMatch(/threshold: \[0, 0\.1, 0\.25, 0\.5, 0\.75, 1\]/);
  });

  test('reduced motion never opts in, so no observer work happens', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain('prefers-reduced-motion: reduce');
    expect(reveal).toMatch(/if\s*\(!el\s*\|\|\s*prefersReducedMotion\(\)\)/);
  });

  test('the reveal observer releases each element and tears down on unmount', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain('unobserve');
    expect(reveal).toContain('disconnect');
  });

  test('each section reveals exactly once — nothing re-hides on scroll up', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    // The element is unobserved the moment it arrives, and no code path ever
    // sets the attribute back to 'out'.
    expect(reveal).toMatch(/reveal\(entry\.target\);\s*\n\s*observer\?\.unobserve\(entry\.target\);/);
    const setsOut = [...reveal.matchAll(/setAttribute\('data-reveal', 'out'\)/g)];
    expect(setsOut.length, 'the hidden state is set once, before observing').toBe(1);
  });

  test('revealing never re-renders the tree — the attribute is set on the DOM node', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain("setAttribute('data-reveal', 'in')");
    expect(reveal).not.toContain('useState');
  });
});

// ── 4b. Which sections move, and for how long ─────────────────
test.describe('section motion', () => {
  const animated: [string, string][] = [
    ['operating-layer-section.tsx', 'opStage'],
    ['incident-lifecycle-section.tsx', 'lcFlow'],
    ['product-console-section.tsx', 'consoleFrameWrap'],
    ['evidence-ai-section.tsx', 'evGrid'],
    ['rwa-security-section.tsx', 'cardGrid4'],
    ['policy-automation-section.tsx', 'polLanes'],
    ['teams-section.tsx', 'cardGrid3'],
  ];

  for (const [file, marker] of animated) {
    test(`${file} reveals on scroll`, () => {
      const source = read(HOME_DIR, file);
      expect(source).toContain('ScrollReveal');
      expect(source).toContain(marker);
    });
  }

  test('every light section heading reveals, so the eye has something to follow', () => {
    for (const [file] of animated) {
      const source = read(HOME_DIR, file);
      if (file === 'operating-layer-section.tsx') {
        // This one choreographs its head by name from the section's single
        // trigger rather than using the shared 0/80/160ms helper.
        for (const part of ['opEyebrow', 'opTitle', 'opLead']) {
          expect(source, `operating layer must sequence ${part}`).toContain(part);
        }
        continue;
      }
      expect(source, `${file} heading must reveal`).toContain('revealHead');
    }
  });

  test('the hero renders immediately and is never gated on JavaScript', () => {
    const hero = read(HOME_DIR, 'hero-section.tsx');
    expect(hero).not.toContain('ScrollReveal');
    expect(hero).not.toContain("'use client'");
  });

  test('pricing and the final CTA stay completely static', () => {
    for (const file of ['pricing-section.tsx', 'final-cta.tsx']) {
      const source = read(HOME_DIR, file);
      expect(source, `${file} must not animate`).not.toContain('ScrollReveal');
      expect(source, `${file} must not animate`).not.toContain('reveal');
    }
  });

  test('the once-only sequences finish inside their budgets', () => {
    const source = css();
    const longest = (marker: string): number => {
      const delays = [...source.matchAll(new RegExp(`${marker}[^\n]*transition-delay:\\s*(\\d+)ms`, 'g'))]
        .map((match) => Number(match[1]));
      return delays.length ? Math.max(...delays) : 0;
    };
    // Incident lifecycle: 1.8–2.2s end to end (last delay + a 0.44s step).
    expect(longest('lcFlow\\[data-reveal=.in.\\]') + 440).toBeGreaterThanOrEqual(1800);
    expect(longest('lcFlow\\[data-reveal=.in.\\]') + 440).toBeLessThanOrEqual(2200);
    // Human-controlled response: ~2s across all seven steps.
    expect(longest('polLanes\\[data-reveal=.in.\\]') + 520).toBeGreaterThanOrEqual(1800);
    expect(longest('polLanes\\[data-reveal=.in.\\]') + 520).toBeLessThanOrEqual(2200);
    // The operating layer's lifecycle line: 2.0–2.5s from the first node
    // lighting to the last, and the line has to be LINEAR — the node delays
    // below are positions along it, so an eased draw would desynchronise them.
    expect(source).toMatch(/\.opStage\[data-reveal='in'\] \.ribbonTrackFill \{[^}]*transition: transform 1\.9s linear 800ms/s);
    const firstNode = 800;
    const lastNode = longest('opStage\\[data-reveal=.in.\\] \\.ribbonStep');
    expect(lastNode - firstNode + 300).toBeGreaterThanOrEqual(2000);
    expect(lastNode - firstNode + 300).toBeLessThanOrEqual(2500);
    // Same rule for the lifecycle connector, for the same reason.
    expect(source).toMatch(/\.lcFlow\[data-reveal='in'\] \.lcTrackFill \{[^}]*transition: transform 1\.75s linear/s);
  });

  test('the console sequence runs once and points at real preview tiles', () => {
    const source = css();
    expect(source).toContain('@keyframes conFocus');
    const rules = [...source.matchAll(/animation:\s*conFocus[^;]*;/g)].map((match) => match[0]);
    expect(rules.length).toBe(4);
    for (const rule of rules) {
      expect(rule, `console sequence must not loop: ${rule}`).not.toContain('infinite');
      expect(rule).toContain('forwards');
    }
  });

  test('the four pillars activate one at a time, not as one block', () => {
    const source = css();
    const delays = [0, ...[...source.matchAll(/\.opStage\[data-reveal='in'\] \.pillar:nth-child\(\d\) \{ transition-delay: (\d+)ms; \}/g)]
      .map((match) => Number(match[1]))];
    expect(delays).toEqual([0, 200, 400, 600]);
    // An 80ms gap under a 650ms transition means all four cards are in motion
    // together and the eye reads one fade, not a sequence. The gap has to be
    // wide enough to be read as an order.
    for (let i = 1; i < delays.length; i += 1) {
      expect(delays[i] - delays[i - 1], 'cards must be clearly sequential').toBeGreaterThanOrEqual(180);
    }
  });

  test('the operating layer observes its head and its sequence separately', () => {
    const source = read(HOME_DIR, 'operating-layer-section.tsx');
    // One observer on the whole ~800px section is what fired the choreography
    // while the pillars and the ribbon were still below the fold.
    expect((source.match(/<ScrollReveal/g) ?? []).length).toBe(2);
    expect(source).toContain('styles.opHead');
    expect(source).toContain('styles.opStage');
    // The pillars and the ribbon stay in ONE group: the ribbon explains the
    // cards above it, so it must draw while they are still arriving.
    const stage = source.slice(source.indexOf('styles.opStage'));
    expect(stage).toContain('styles.pillars');
    expect(stage).toContain('styles.ribbon');
  });

  test('the lifecycle ribbon starts muted and ends verified', () => {
    const source = css();
    // "All nodes inactive" is a staged state only. The resting style — what a
    // no-JS or reduced-motion visitor sees — is the COMPLETED lifecycle, so a
    // finished diagram is never shown as if nothing had run.
    expect(source).toMatch(/\.opStage\[data-reveal='out'\] \.ribbonNode \{[^}]*border-color: var\(--border-strong\)/s);
    expect(source).toMatch(/\.opStage\[data-reveal='out'\] \.ribbonLabel \{[^}]*color: var\(--text-3\)/s);
    expect(source).toMatch(/\.ribbonNode \{[^}]*border: 2px solid var\(--brand\)/s);
    expect(source).toMatch(/\.ribbonStep:last-child \.ribbonNode \{[^}]*var\(--healthy\)/s);
    // The connector itself never carries green: scaleX scales its own paint,
    // so a gradient ending green would read "proven" at OBSERVE.
    expect(source).toMatch(/\.ribbonTrackFill \{[\s\S]*?background: linear-gradient\(90deg, #0e7490, var\(--brand\)\);/);
    expect(source).not.toMatch(/\.ribbonTrackFill \{[\s\S]*?background: linear-gradient\([^)]*#15803d/);
  });
});

// ── 5. CSP: the page may not use inline styles ────────────────
test('no landing component uses an inline style attribute', () => {
  // Production CSP is `style-src 'self' 'nonce-…'`, so a style attribute would
  // be blocked outright. Every tone, stagger and delay is a class instead.
  for (const file of componentFiles()) {
    expect(file.source, `${file.name} must not use a style attribute`).not.toMatch(/\sstyle=\{/);
    expect(file.source, `${file.name} must not use a style attribute`).not.toMatch(/\sstyle="/);
  }
});

// ── 6. Accessibility guarantees ───────────────────────────────
test.describe('accessibility', () => {
  test('anchored sections clear the sticky header', () => {
    expect(css()).toMatch(/\.section\s*\{[^}]*scroll-margin-top/s);
  });

  test('focus is always visible, on both the light and the dark zones', () => {
    const source = css();
    expect(source).toContain('a:focus-visible');
    expect(source).toContain('button:focus-visible');
    expect(source).toContain('outline: 2px solid var(--focus)');
    expect(source).toContain('.zoneDark a:focus-visible');
  });

  test('interactive controls meet the touch-target floor', () => {
    const source = css();
    expect(source).toMatch(/\.btnPrimary,\s*\n\.btnSecondary\s*\{[^}]*min-height:\s*44px/s);
    expect(source).toMatch(/\.hamburger\s*\{[^}]*width:\s*44px[^}]*height:\s*44px/s);
    expect(source).toMatch(/\.brand\s*\{[^}]*min-height:\s*44px/s);
  });

  test('the mobile menu button is wired to the panel it controls', () => {
    const header = read(HOME_DIR, 'marketing-header.tsx');
    expect(header).toContain('aria-expanded={open}');
    expect(header).toContain('aria-controls="marketing-mobile-nav"');
    expect(header).toContain('id="marketing-mobile-nav"');
  });

  test('the page covers every required breakpoint', () => {
    const source = css();
    for (const width of [1200, 1024, 860, 720, 420]) {
      expect(source, `missing breakpoint ${width}px`).toContain(`@media (max-width: ${width}px)`);
    }
  });
});

// ── 7. Truthfulness of the redesigned surfaces ────────────────
test.describe('content safety', () => {
  test('the console preview says in plain language that it is not live data', () => {
    const data = read(HOME_DIR, 'home-data.ts');
    expect(data).toContain('CONSOLE_PREVIEW_NOTE');
    expect(data).toContain('not live customer data');
    const consoleSection = read(HOME_DIR, 'product-console-section.tsx');
    expect(consoleSection).toContain('CONSOLE_PREVIEW_NOTE');
    expect(consoleSection).toContain('Product preview');
  });

  test('the hero workflow stays labelled as an illustration', () => {
    const demo = read(HOME_DIR, 'incident-workflow-demo.tsx');
    expect(demo).toContain('EXAMPLE INCIDENT WORKFLOW');
    expect(demo).toContain('Illustration');
  });

  test('no customers, certifications, awards or production metrics are invented', () => {
    const source = componentFiles().map((f) => f.source).join('\n') + read(HOME_DIR, 'home-data.ts');
    for (const claim of [
      'SOC 2',
      'ISO 27001',
      'Trusted by',
      'trusted by',
      'customers worldwide',
      'assets protected',
      'funds saved',
      'testimonial',
      'Testimonial',
      'award',
      'Award',
    ]) {
      expect(source, `unexpected unfounded claim "${claim}"`).not.toContain(claim);
    }
  });

  test('severity colours stay semantic on both surfaces', () => {
    const source = css();
    expect(source).toContain('--critical: #b91c1c');
    expect(source).toContain('--high: #c2410c');
    expect(source).toContain('--medium: #b45309');
    expect(source).toContain('--healthy: #15803d');
    expect(source).toContain('--info: #1d4ed8');
  });
});

// ── 8. Isolation from the authenticated product ───────────────
test('the redesign cannot reach the authenticated product', () => {
  // home.module.css is a CSS module, and only the landing tree imports it.
  const appFiles: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
        walk(full);
      } else if (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) {
        appFiles.push(full);
      }
    }
  };
  walk(APP_DIR);

  const importers = appFiles
    .filter((file) => read(file).includes('home.module.css'))
    .map((file) => path.relative(APP_DIR, file).replace(/\\/g, '/'));

  for (const importer of importers) {
    expect(importer, `${importer} must not import the landing stylesheet`)
      .toMatch(/^(page\.tsx|components\/home\/)/);
  }
});

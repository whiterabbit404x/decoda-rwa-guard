import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Source-level guardrails for the public website's design system — the
 * www.decodasecurity.com visual language applied to the Guard public pages.
 * Like the sibling homepage specs these run without a web server, so they stay
 * reliable in CI. homepage-motion-render.spec.ts measures the same motion in a
 * real browser.
 *
 * What they pin:
 *   • the company website's tokens, defined once on the public root
 *   • the motion budget: which loops exist, and that every keyframe and
 *     transition animates compositor properties only
 *   • reduced motion landing on the finished page, never a blank one
 *   • content that is readable without JavaScript
 *   • the CSP constraint that forbids inline styles
 *   • no invented customers, certifications or live metrics
 *   • isolation from the authenticated product
 */

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

/** Every public-site stylesheet (CSS Modules under components/home). */
const STYLESHEETS = fs.readdirSync(HOME_DIR).filter((file) => file.endsWith('.module.css'));

function stylesheet(file: string): string {
  return read(HOME_DIR, file);
}

const siteCss = () => stylesheet('site.module.css');

/** Source with comments removed: what can actually reach a visitor. */
function visible(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ');
}

function componentFiles(): { name: string; source: string }[] {
  return fs
    .readdirSync(HOME_DIR)
    .filter((file) => file.endsWith('.tsx'))
    .map((file) => ({ name: file, source: read(HOME_DIR, file) }));
}

/** The public pages that render inside <PublicShell>. */
const PUBLIC_PAGES = [
  ['page.tsx'],
  ['pricing', 'page.tsx'],
  ['trust', 'page.tsx'],
  ['security', 'page.tsx'],
  ['privacy', 'page.tsx'],
  ['terms', 'page.tsx'],
  ['support', 'page.tsx'],
  ['live-proof', 'page.tsx'],
  ['request-pilot', 'page.tsx'],
  ['request-pilot', 'request-pilot-view.tsx'],
  ['request-pilot', 'request-pilot-client.tsx'],
];

/**
 * The brace-balanced body of the block whose header matches `header`. A
 * `[^}]*` regex stops at the first nested `}`, so a multi-step @keyframes
 * would come back as its first step; counting braces reads the whole block.
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

/** Every @keyframes body in a stylesheet, whole. */
function keyframeBodies(source: string): { name: string; body: string }[] {
  return [...source.matchAll(/@keyframes\s+([\w-]+)/g)].map((match) => ({
    name: match[1],
    body: blockBody(source, new RegExp(`@keyframes\\s+${match[1]}\\b`)),
  }));
}

/**
 * Style rules, comments stripped, @keyframes/@font-face dropped and @media /
 * @supports recursed into. Brace-balanced, so a one-line keyframe block cannot
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
    if (selector.startsWith('@media') || selector.startsWith('@supports')) {
      rules.push(...styleRules(body));
    } else if (!selector.startsWith('@')) {
      rules.push({ selector, body });
    }
    cursor = end;
  }
  return rules;
}

// ── 1. Brand tokens ───────────────────────────────────────────
test.describe('brand tokens', () => {
  test('the company website tokens are defined once, on the public root', () => {
    const root = blockBody(siteCss(), /^\.site\s*\{/m);
    for (const token of [
      '--bg: #ffffff',
      '--bg-soft: #f6f8fb',
      '--text: #0b1a2e',
      '--muted: #56657b',
      '--accent: #0b7a80',
      '--accent-bright: #14b8a6',
      '--accent-cyan: #06b6d4',
      '--btn-primary-bg: #0b1a2e',
      '--border: #e3e8ef',
    ]) {
      expect(root, `expected public token "${token}" on .site`).toContain(token);
    }
    // One root: no other stylesheet redefines the light palette. (A navy
    // subtree, such as the console window, may re-point tokens to their navy
    // values — as the company website's surface-dark does.)
    for (const file of STYLESHEETS.filter((name) => name !== 'site.module.css')) {
      expect(stylesheet(file), `${file} redefines the light --text`).not.toContain('--text: #0b1a2e');
      expect(stylesheet(file), `${file} redefines the light --accent`).not.toContain('--accent: #0b7a80');
      expect(stylesheet(file), `${file} redefines the page background`).not.toContain('--bg: #ffffff');
    }
  });

  test('the public pages render light, whatever the product theme is set to', () => {
    const source = siteCss();
    expect(source).toMatch(/\.site\s*\{[\s\S]*?color-scheme:\s*light;/);
    expect(source).toMatch(/\.site\s*\{[\s\S]*?background:\s*var\(--bg\);/);
  });

  test('no surface is pure black', () => {
    for (const file of STYLESHEETS) {
      const source = stylesheet(file);
      expect(source, file).not.toMatch(/#000\b/);
      expect(source, file).not.toContain('#000000');
      expect(source, file).not.toMatch(/\brgb\(\s*0\s*,\s*0\s*,\s*0\s*\)/);
    }
  });

  test('the monospace face is self-hosted from this origin', () => {
    const source = siteCss();
    expect(source).toContain("font-family: 'Decoda Mono'");
    expect(source).toContain("url('../../fonts/JetBrainsMono-latin-400.woff2')");
    expect(source).toContain("url('../../fonts/JetBrainsMono-latin-500.woff2')");
    expect(source).not.toMatch(/fonts\.googleapis|fonts\.gstatic|https?:\/\//);
    expect(fs.existsSync(path.join(APP_DIR, 'fonts', 'JetBrainsMono-LICENSE.txt'))).toBe(true);
  });
});

// ── 2. Motion budget ──────────────────────────────────────────
test.describe('animation budget', () => {
  test('the only looping animations are the known decorative ones', () => {
    const loops: string[] = [];
    for (const file of STYLESHEETS) {
      for (const match of stylesheet(file).matchAll(/animation:\s*([\w-]+)[^;]*\binfinite\b[^;]*;/g)) {
        loops.push(`${file}:${match[1]}`);
      }
    }
    // A loop added anywhere else has to be added here, deliberately. None of
    // these is data: a pulsing dot, a radar sweep, drifting background glows,
    // a blinking cursor and a gently bobbing card.
    expect(loops.sort()).toEqual(
      [
        'console.module.css:blink',
        'console.module.css:bob',
        'home.module.css:blip',
        'home.module.css:pill-ping',
        'home.module.css:radar-spin',
        'site.module.css:badge-pulse',
        'site.module.css:glow-drift-a',
        'site.module.css:glow-drift-a',
        'site.module.css:glow-drift-b',
        'site.module.css:sweep',
      ].sort(),
    );
  });

  test('every keyframe animates only opacity and transform', () => {
    let count = 0;
    for (const file of STYLESHEETS) {
      for (const { name, body } of keyframeBodies(stylesheet(file))) {
        count += 1;
        expect(body, `${file} @keyframes ${name} came back empty`).not.toBe('');
        for (const property of [...body.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1])) {
          expect(property, `${file} @keyframes ${name} animates "${property}"`).toMatch(/^(opacity|transform)$/);
        }
      }
    }
    expect(count).toBeGreaterThan(10);
  });

  test('no transition moves a layout property', () => {
    const layout = /^(width|height|min-width|max-width|min-height|max-height|top|left|right|bottom|inset|margin\S*|padding\S*|grid-template\S*|flex\S*|gap|font-size|line-height|letter-spacing|all)$/;
    for (const file of STYLESHEETS) {
      const clean = stylesheet(file).replace(/\/\*[\s\S]*?\*\//g, '');
      for (const match of clean.matchAll(/transition(?:-property)?\s*:\s*([^;]+);/g)) {
        for (const part of match[1].split(',')) {
          const property = part.trim().split(/\s+/)[0];
          expect(property, `${file}: "transition: ${match[1].trim()}" moves layout`).not.toMatch(layout);
        }
      }
    }
  });

  test('microinteractions use the 150–300ms tokens', () => {
    const root = blockBody(siteCss(), /^\.site\s*\{/m);
    expect(root).toContain('--dur-fast: 160ms');
    expect(root).toContain('--dur: 280ms');
    expect(root).toContain('--ease-out: cubic-bezier(0.22, 1, 0.36, 1)');
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

  test('nothing on the page counts, ticks or fabricates a live number', () => {
    for (const file of componentFiles()) {
      expect(file.source, `${file.name} runs an interval`).not.toContain('setInterval');
      expect(file.source, `${file.name} animates per frame`).not.toContain('requestAnimationFrame');
    }
  });
});

// ── 3. Reduced motion ─────────────────────────────────────────
test.describe('reduced motion', () => {
  test('reduced motion switches every animation off and unhides staged content', () => {
    const source = siteCss();
    const block = blockBody(source, /@media \(prefers-reduced-motion: reduce\)/);
    expect(block).toContain('scroll-behavior: auto');
    expect(block).toMatch(/\.site \*,\s*\.site \*::before,\s*\.site \*::after\s*\{[^}]*animation: none !important/);
    expect(block).toContain('transition-duration: 0.001ms !important');
    expect(block).toMatch(/\.reveal\[data-reveal='out'\],\s*\.revealGroup\[data-reveal='out'\] > \*\s*\{[^}]*opacity: 1 !important/);
  });

  test('the hero console never starts its loop under reduced motion', () => {
    const console = read(HOME_DIR, 'guard-console.tsx');
    // It renders the COMPLETED workflow first and only restarts from Detect
    // when motion is allowed.
    expect(console).toContain('useState<number>(LAST)');
    expect(console).toMatch(/matchMedia\('\(prefers-reduced-motion: reduce\)'\)\.matches\)\)\s*\{\s*return undefined;/);
  });

  test('the hero console pauses off screen and in a background tab', () => {
    const console = read(HOME_DIR, 'guard-console.tsx');
    expect(console).toContain('if (!visible || document.hidden) return;');
    expect(console).toContain("document.addEventListener('visibilitychange', onVisibility)");
    expect(console).toContain('observer?.disconnect()');
  });
});

// ── 4. Scroll reveal ──────────────────────────────────────────
test.describe('scroll reveal', () => {
  test('content is readable without JavaScript — hiding is keyed on attributes the server render satisfies', () => {
    // Every rule that sets `opacity: 0` is either keyed on data-reveal (an
    // attribute only scroll-reveal.tsx sets, after hydration), or hides an
    // ALTERNATE state of an aria-hidden illustration whose server render shows
    // one complete state.
    const alternates = new Set(['.panel', '.float', '.stagePanel']);
    const hiding: string[] = [];
    for (const file of STYLESHEETS) {
      for (const rule of styleRules(stylesheet(file))) {
        if (!/opacity:\s*0\s*;/.test(rule.body)) continue;
        hiding.push(rule.selector);
        const keyed = rule.selector.includes("data-reveal='out'");
        const decorative = rule.selector.split(',').every((part) => /::(after|before)\b/.test(part));
        expect(keyed || decorative || alternates.has(rule.selector), `${file}: "${rule.selector}" hides content without JavaScript`).toBe(true);
      }
    }
    expect(hiding.length).toBeGreaterThan(0);
    // …and each alternate set has a member that is visible in the server render.
    expect(stylesheet('console.module.css')).toMatch(/\.panel\[data-active\]\s*\{[^}]*opacity: 1/);
    expect(stylesheet('console.module.css')).toMatch(/\.float\[data-on\]\s*\{[^}]*opacity: 1/);
    expect(stylesheet('story.module.css')).toMatch(/\.stagePanel\[data-active\]\s*\{[^}]*opacity: 1/);
    expect(read(HOME_DIR, 'guard-story.tsx')).toContain('useState(0)');
  });

  test('the staged state declares no animation, and the entrance fills backwards', () => {
    const source = siteCss();
    const out = blockBody(source, /\.reveal\[data-reveal='out'\],\s*\.revealGroup\[data-reveal='out'\] > \*\s*/);
    expect(out).toMatch(/opacity:\s*0/);
    expect(out).not.toContain('animation');
    expect(out).not.toContain('transition');
    expect(source).toMatch(/\.reveal\[data-reveal='in'\]\s*\{[^}]*animation: reveal-up var\(--dur-slow\) var\(--ease-out\) backwards/);
    expect(source).toMatch(/\.revealGroup\[data-reveal='in'\] > \*\s*\{[^}]*backwards/);
  });

  test('entrances travel a readable distance, no further', () => {
    const source = siteCss();
    for (const name of ['reveal-up', 'reveal-scale', 'reveal-left', 'reveal-right']) {
      const body = blockBody(source, new RegExp(`@keyframes ${name}\\b`));
      const distances = [...body.matchAll(/translate3d\((-?\d+)(?:px)?,\s*(-?\d+)(?:px)?/g)].map((m) => Math.max(Math.abs(Number(m[1])), Math.abs(Number(m[2]))));
      expect(distances.length, `${name} has no travel`).toBeGreaterThan(0);
      for (const distance of distances) {
        expect(distance, `${name} travels ${distance}px`).toBeGreaterThanOrEqual(16);
        expect(distance, `${name} travels ${distance}px`).toBeLessThanOrEqual(36);
      }
    }
  });

  test('the trigger is measured in screen pixels, not as a percent of the element', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain('const REVEAL_RATIO = 0.22');
    expect(reveal).toContain('const READING_INSET = 0.12');
    expect(reveal).toContain('const MIN_READING_PX = 150');
    expect(reveal).toContain('const MAX_READING_SHARE = 0.42');
    expect(reveal).toMatch(/Math\.max\(blockHeight \* REVEAL_RATIO, MIN_READING_PX\)/);
    expect(reveal).toMatch(/Math\.min\(wanted, blockHeight, viewport \* MAX_READING_SHARE\)/);
    expect(reveal).toContain('rootMargin: `0px 0px -${READING_INSET * 100}% 0px`');
  });

  test('the load-time check and the scroll trigger share ONE predicate', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect((reveal.match(/function arrived\(/g) ?? []).length).toBe(1);
    expect(reveal).toMatch(/arrived\(entry\.boundingClientRect, entry\.intersectionRect\.height\)/);
    expect(reveal).toMatch(/return arrived\(rect, visibleHeight\(rect\)\)/);
    expect(reveal).toContain('if (hasArrived(el))');
    expect(reveal).toMatch(/threshold: \[0, 0\.1, 0\.25, 0\.5, 0\.75, 1\]/);
  });

  test('reduced motion never opts in, so no observer work happens', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain('prefers-reduced-motion: reduce');
    expect(reveal).toMatch(/if\s*\(!el\s*\|\|\s*prefersReducedMotion\(\)\)/);
  });

  test('each block reveals exactly once and the observer is released', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toMatch(/entry\.target\.setAttribute\('data-reveal', 'in'\);\s*\n\s*observer\?\.unobserve\(entry\.target\);/);
    expect([...reveal.matchAll(/setAttribute\('data-reveal', 'out'\)/g)].length, 'the hidden state is set once, before observing').toBe(1);
    expect(reveal).toContain('disconnect()');
  });

  test('revealing never re-renders the tree — the attribute is set on the DOM node', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).not.toContain('useState');
  });
});

// ── 5. The hero ───────────────────────────────────────────────
test.describe('hero', () => {
  test('the headline and calls to action are server-rendered and never wait for a reveal', () => {
    const hero = read(HOME_DIR, 'hero-section.tsx');
    expect(hero).not.toContain("'use client'");
    const copy = hero.slice(hero.indexOf('<div className={styles.heroCopy}>'), hero.indexOf('<div className={styles.heroVisual}>'));
    expect(copy).toContain('<h1 id="hero-title"');
    expect(copy).toContain('Request Guard Pilot');
    expect(copy).not.toContain('<Reveal');
    expect(copy).not.toContain('<RevealGroup');
  });

  test('the headline entrance is CSS only and disabled under reduced motion', () => {
    const css = stylesheet('home.module.css');
    const reduced = blockBody(css, /@media \(prefers-reduced-motion: reduce\)/);
    expect(reduced).toContain('.line > span');
    expect(reduced).toContain('animation: none !important');
  });
});

// ── 6. CSP: no inline styles anywhere on the public site ──────
test('no public component or page uses an inline style attribute', () => {
  // Production CSP is `style-src 'self' 'nonce-…'`, so a style attribute would
  // be blocked outright. Every tone, stagger and delay is a class instead.
  const sources = [
    ...componentFiles(),
    ...PUBLIC_PAGES.map((segments) => ({ name: segments.join('/'), source: read(APP_DIR, ...segments) })),
  ];
  for (const file of sources) {
    expect(file.source, `${file.name} must not use a style attribute`).not.toMatch(/\sstyle=\{/);
    expect(file.source, `${file.name} must not use a style attribute`).not.toMatch(/\sstyle="/);
  }
});

// ── 7. Accessibility guarantees ───────────────────────────────
test.describe('accessibility', () => {
  test('every public page has a skip link to the main landmark', () => {
    const shell = read(HOME_DIR, 'public-shell.tsx');
    expect(shell).toContain('<a href="#main" className={styles.skipLink}>');
    expect(shell).toContain('<main id="main"');
    expect(siteCss()).toMatch(/\.skipLink:focus-visible\s*\{/);
  });

  test('anchored sections clear the sticky header', () => {
    expect(siteCss()).toMatch(/\.section\s*\{[^}]*scroll-margin-top:\s*calc\(var\(--header-h\) \+ 8px\)/s);
  });

  test('focus is always visible, on light and on navy surfaces', () => {
    const source = siteCss();
    expect(source).toMatch(/:where\(\.site\) :focus-visible\s*\{[^}]*outline: 2px solid var\(--accent-bright\)/);
    expect(source).toMatch(/:where\(\.site\) \.surfaceDark :focus-visible\s*\{[^}]*outline-color/);
    // A rule may drop the outline only to draw an equivalent ring, or on an
    // element that is focused programmatically and is not interactive.
    const programmatic = new Set(['.main:focus', '.confirmation h2:focus-visible']);
    for (const file of STYLESHEETS) {
      for (const rule of styleRules(stylesheet(file))) {
        if (!/:focus/.test(rule.selector) || !/outline:\s*(none|0)\b/.test(rule.body)) continue;
        expect(
          programmatic.has(rule.selector) || /box-shadow:\s*0 0 0 \d+px/.test(rule.body),
          `${file}: "${rule.selector}" removes the focus ring without a replacement`,
        ).toBe(true);
      }
    }
  });

  test('interactive controls meet the 44px touch-target floor', () => {
    const source = siteCss();
    expect(source).toMatch(/\.btnPrimary,\s*\n\.btnSecondary,\s*\n\.btnGhost\s*\{[^}]*min-height:\s*44px/s);
    expect(source).toMatch(/\.menuButton\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
    expect(source).toMatch(/\.brand\s*\{[^}]*min-height:\s*44px/s);
  });

  test('the mobile menu button is wired to the panel it controls', () => {
    const header = read(HOME_DIR, 'marketing-header.tsx');
    expect(header).toContain('aria-expanded={open}');
    expect(header).toContain('aria-controls="marketing-mobile-nav"');
    expect(header).toContain('id="marketing-mobile-nav"');
  });

  test('the product tour is a WAI-ARIA tab set driven by the keyboard', () => {
    const showcase = read(HOME_DIR, 'product-showcase.tsx');
    expect(showcase).toContain('role="tablist"');
    expect(showcase).toContain('role="tab"');
    expect(showcase).toContain('role="tabpanel"');
    expect(showcase).toContain('aria-selected={index === active}');
    expect(showcase).toContain('tabIndex={index === active ? 0 : -1}');
    for (const key of ["'ArrowDown'", "'ArrowUp'", "'Home'", "'End'"]) expect(showcase).toContain(key);
  });

  test('the layout adapts at phone, tablet and desktop widths', () => {
    const all = STYLESHEETS.map(stylesheet).join('\n');
    for (const width of [980, 720, 560, 420]) {
      expect(all, `missing breakpoint ${width}px`).toContain(`@media (max-width: ${width}px)`);
    }
  });
});

// ── 8. Truthfulness of the public surfaces ────────────────────
test.describe('content safety', () => {
  test('every product illustration says it is an illustration with sample data', () => {
    const data = read(HOME_DIR, 'home-data.ts');
    expect(data).toContain("export const ILLUSTRATION_NOTE = 'Illustrative interface · sample data'");
    for (const file of ['guard-console.tsx', 'guard-story.tsx', 'product-showcase.tsx']) {
      expect(read(HOME_DIR, file), `${file} must show the illustration note`).toContain('ILLUSTRATION_NOTE');
    }
    const console = read(HOME_DIR, 'guard-console.tsx');
    expect(console).toContain('Sample incident');
    expect(console).toContain('INC-SAMPLE');
    // Its made-up values are hidden from assistive technology; the caption
    // describes the illustration instead.
    expect(console).toContain('<div className={styles.frame} aria-hidden="true">');
    expect(console).toContain('Illustration with sample data');
  });

  test('no customers, certifications, awards or production metrics are invented', () => {
    const source = visible(componentFiles().map((f) => f.source).join('\n') + read(HOME_DIR, 'home-data.ts'));
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
      'uptime',
      '99.9',
    ]) {
      expect(source, `unexpected unfounded claim "${claim}"`).not.toContain(claim);
    }
  });

  test('the page never implies autonomous execution', () => {
    const source = visible(componentFiles().map((f) => f.source).join('\n') + read(HOME_DIR, 'home-data.ts'));
    expect(source).not.toMatch(/autonomous(ly)? (execut|respond|remediat)/i);
    expect(source).not.toMatch(/auto-?remediat/i);
    expect(source).toContain('recommend-only');
  });

  test('status colours stay semantic', () => {
    const root = blockBody(siteCss(), /^\.site\s*\{/m);
    expect(root).toContain('--ok: #047857');
    expect(root).toContain('--warn: #b45309');
    expect(root).toContain('--danger: #b91c1c');
  });
});

// ── 9. Isolation from the authenticated product ───────────────
test('the public stylesheets cannot reach the authenticated product', () => {
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

  const publicRoots = new Set(PUBLIC_PAGES.map((segments) => segments.join('/')));
  for (const sheet of STYLESHEETS) {
    const importers = appFiles
      .filter((file) => read(file).includes(sheet))
      .map((file) => path.relative(APP_DIR, file).replace(/\\/g, '/'));
    expect(importers.length, `${sheet} is unused`).toBeGreaterThan(0);
    for (const importer of importers) {
      expect(
        importer.startsWith('components/home/') || publicRoots.has(importer),
        `${importer} imports the public stylesheet ${sheet}`,
      ).toBe(true);
    }
  }

  // Every rule hangs off the public root or a module-local class: the only
  // global selectors are the html/body light-mode lock scoped by :has(.site).
  for (const sheet of STYLESHEETS) {
    for (const match of stylesheet(sheet).matchAll(/:global\(([^)]*)\)/g)) {
      expect(['html', 'body'], `${sheet} styles a global selector "${match[1]}"`).toContain(match[1]);
    }
  }
});

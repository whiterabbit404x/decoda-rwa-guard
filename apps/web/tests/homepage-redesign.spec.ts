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
    // ~7.4s sequence plus a settled hold, then a soft reset.
    expect(source).toContain('animation: wfStage1 10s linear infinite');
    expect(source).toContain('animation: wfRailAdvance 10s linear infinite');
  });

  test('the workflow animates only opacity and transform — never layout', () => {
    const source = css();
    const keyframeBlocks = source.match(/@keyframes\s+(wfStage\d|wfRailAdvance|wfGlow\d)\s*\{[^}]*(\{[^}]*\}[^}]*)*\}/g) ?? [];
    expect(keyframeBlocks.length).toBeGreaterThan(0);
    for (const block of keyframeBlocks) {
      for (const banned of ['width:', 'height:', 'top:', 'left:', 'margin', 'padding']) {
        expect(block, `keyframe animates layout property "${banned}"`).not.toContain(banned);
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
    expect(source).toMatch(/@keyframes wfRailAdvance\s*\{[^}]*0%,\s*1%\s*\{\s*transform:\s*scaleY\(0\)/s);
    // Each stage's keyframes start dimmed and settle at full opacity.
    expect(source).toMatch(/@keyframes wfStage1\s*\{[^}]*opacity:\s*0\.26/s);
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
  test('content is readable without JavaScript — the hidden state is opt-in', () => {
    const source = css();
    // The hidden start state only applies once the client sets the flag, so a
    // visitor whose JS never runs reads the finished page.
    const hiddenRules = source
      .split('\n')
      .filter((line) => line.includes('.reveal') && line.includes('{'));
    for (const rule of hiddenRules) {
      if (rule.includes('opacity: 0') || rule.includes('data-reveal')) {
        expect(rule, `reveal rule must be gated on the client flag: ${rule.trim()}`)
          .toContain("html[data-landing-reveal='on']");
      }
    }
    expect(source).toContain(":global(html[data-landing-reveal='on']) .reveal {");
  });

  test('reduced motion never opts in, so no observer work happens', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain('prefers-reduced-motion: reduce');
    expect(reveal).toMatch(/if\s*\(!el\s*\|\|\s*prefersReducedMotion\(\)\)/);
  });

  test('the reveal observer releases each element and clears the flag on unmount', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain('unobserve');
    expect(reveal).toContain('disconnect');
    expect(reveal).toContain('delete document.documentElement.dataset[REVEAL_FLAG]');
  });

  test('revealing never re-renders the tree — the attribute is set on the DOM node', () => {
    const reveal = read(HOME_DIR, 'scroll-reveal.tsx');
    expect(reveal).toContain("setAttribute('data-reveal', 'in')");
    expect(reveal).not.toContain('useState');
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

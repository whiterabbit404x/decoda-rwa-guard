/**
 * A REAL-COMPONENT render harness for the Next.js app sources.
 *
 * Why this exists
 * ---------------
 * Most specs in this directory are pure-logic tests over the presentation
 * modules plus `toContain` assertions against component source text. Those pin
 * wiring, but they cannot prove that the actual React tree renders, that a row
 * click opens a panel, or that a backend PASS/FAIL/UNKNOWN reaches the DOM. A
 * component that threw on render would still satisfy a source-text assertion.
 *
 * This harness mounts the UNMODIFIED `apps/web/app` components in Chromium:
 *
 *   * TypeScript's own transpiler compiles each `.ts`/`.tsx` on demand — the
 *     real file, never a hand-written DOM copy of it,
 *   * relative imports are resolved to served module URLs so the genuine
 *     dependency graph (ui-primitives, presentation, operational-integrity …)
 *     is what actually executes,
 *   * the handful of framework/bare specifiers the app pulls in
 *     (`react`, `react-dom`, `next/navigation`, `next/link`) resolve to the
 *     shims below, so no Next.js server or build step is required.
 *
 * Nothing here mocks the code under test. The only things stubbed are the
 * framework seams and `fetch`, which each spec supplies so the DOM under
 * assertion is rendered from a KNOWN API payload.
 */
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import ts from 'typescript';

const APP_DIR = path.join(__dirname, '..', '..', 'app');
const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const NODE_MODULES = path.join(REPO_ROOT, 'node_modules');

/** Extension probe order, mirroring the bundler resolution the app builds with. */
const EXTENSIONS = ['.tsx', '.ts', '.jsx', '.js'];

function resolveModuleFile(fromFile: string, specifier: string): string | null {
  const base = path.resolve(path.dirname(fromFile), specifier);
  for (const ext of EXTENSIONS) {
    if (fs.existsSync(base + ext)) return base + ext;
  }
  if (fs.existsSync(base) && fs.statSync(base).isDirectory()) {
    for (const ext of EXTENSIONS) {
      const indexFile = path.join(base, 'index' + ext);
      if (fs.existsSync(indexFile)) return indexFile;
    }
  }
  return fs.existsSync(base) ? base : null;
}

/** Bare specifiers the app imports that this harness serves a shim for. */
const VENDOR: Record<string, string> = {
  react: '/vendor/react.js',
  'react-dom': '/vendor/react-dom.js',
  'react-dom/client': '/vendor/react-dom-client.js',
  'react/jsx-runtime': '/vendor/jsx-runtime.js',
  'react/jsx-dev-runtime': '/vendor/jsx-runtime.js',
  'next/link': '/vendor/next-link.js',
  'next/navigation': '/vendor/next-navigation.js',
  'next/image': '/vendor/next-image.js',
};

/**
 * Rewrite every module specifier in already-transpiled JS to a URL this server
 * can serve.
 *
 * The emitted JS is re-parsed and only genuine module specifiers are touched —
 * the import/export declarations and dynamic import() calls the AST reports.
 * A regex would also rewrite prose: a doc comment containing
 * `apart from "verdict says unhealthy"` looks exactly like `from "…"`.
 *
 * A specifier that is neither relative nor a known vendor shim is a real gap in
 * the harness and fails loudly, rather than silently rendering a component with
 * a missing dependency.
 */
function rewriteSpecifiers(code: string, sourceFile: string): string {
  const parsed = ts.createSourceFile('emit.js', code, ts.ScriptTarget.ES2022, true, ts.ScriptKind.JS);
  const edits: Array<{ start: number; end: number; text: string }> = [];

  const resolve = (specifier: string): string => {
    if (VENDOR[specifier]) return VENDOR[specifier];
    if (specifier.startsWith('.')) {
      const target = resolveModuleFile(sourceFile, specifier);
      if (!target) throw new Error(`render-harness: cannot resolve "${specifier}" from ${sourceFile}`);
      return `/app/${path.relative(APP_DIR, target).split(path.sep).join('/')}`;
    }
    throw new Error(
      `render-harness: unsupported bare import "${specifier}" in ${sourceFile}. ` +
        'Add a shim to VENDOR if the app legitimately depends on it.',
    );
  };

  const record = (node: ts.Node | undefined) => {
    if (!node || !ts.isStringLiteral(node)) return;
    // getStart()/getEnd() include the quotes; replace the literal wholesale.
    edits.push({ start: node.getStart(parsed), end: node.getEnd(), text: JSON.stringify(resolve(node.text)) });
  };

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      record(node.moduleSpecifier);
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      record(node.arguments[0]);
    } else if (ts.isImportTypeNode(node)) {
      record(ts.isLiteralTypeNode(node.argument) ? node.argument.literal : undefined);
    }
    ts.forEachChild(node, visit);
  };
  visit(parsed);

  let output = code;
  for (const edit of edits.sort((a, b) => b.start - a.start)) {
    output = output.slice(0, edit.start) + edit.text + output.slice(edit.end);
  }
  return output;
}

function transpile(file: string): string {
  const source = fs.readFileSync(file, 'utf-8');
  const output = ts.transpileModule(source, {
    fileName: file,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.ReactJSX,
      // `verbatimModuleSyntax` off + isolatedModules on keeps type-only imports
      // from surviving into the emitted graph as unresolvable module requests.
      isolatedModules: true,
      esModuleInterop: true,
    },
  });
  return rewriteSpecifiers(output.outputText, file);
}

/* ── Framework shims ─────────────────────────────────────────────────────── */

const REACT_SHIM = `
const React = window.React;
export default React;
export const {
  Children, Component, Fragment, PureComponent, StrictMode, Suspense, cloneElement,
  createContext, createElement, createRef, forwardRef, isValidElement, lazy, memo,
  startTransition, useCallback, useContext, useDebugValue, useDeferredValue, useEffect,
  useId, useImperativeHandle, useInsertionEffect, useLayoutEffect, useMemo, useReducer,
  useRef, useState, useSyncExternalStore, useTransition, version,
} = React;
`;

const REACT_DOM_SHIM = `
const ReactDOM = window.ReactDOM;
export default ReactDOM;
export const { createPortal, flushSync, findDOMNode } = ReactDOM;
`;

const REACT_DOM_CLIENT_SHIM = `
const ReactDOM = window.ReactDOM;
export const createRoot = ReactDOM.createRoot;
export const hydrateRoot = ReactDOM.hydrateRoot;
export default { createRoot: ReactDOM.createRoot, hydrateRoot: ReactDOM.hydrateRoot };
`;

/**
 * The automatic JSX runtime on top of the UMD build (which ships only
 * `createElement`). `key` is lifted out of the config exactly as the real
 * runtime does, so keyed lists render without spurious React warnings.
 */
const JSX_RUNTIME_SHIM = `
const React = window.React;
export const Fragment = React.Fragment;
export function jsx(type, config, maybeKey) {
  const props = Object.assign({}, config);
  if (maybeKey !== undefined) props.key = maybeKey;
  return React.createElement(type, props);
}
export const jsxs = jsx;
export const jsxDEV = jsx;
`;

/** next/link and next/image reduced to the DOM elements they render. */
const NEXT_LINK_SHIM = `
const React = window.React;
export default function Link({ href, children, ...rest }) {
  return React.createElement('a', { href: typeof href === 'string' ? href : '#', ...rest }, children);
}
`;

const NEXT_IMAGE_SHIM = `
const React = window.React;
export default function Image({ src, alt, ...rest }) {
  return React.createElement('img', { src, alt: alt ?? '', ...rest });
}
`;

/**
 * next/navigation, backed by a test-controlled store on `window.__nav` so a
 * spec can assert what the screen pushed and preset the query string a tab
 * reads on mount.
 */
const NEXT_NAVIGATION_SHIM = `
window.__nav = window.__nav || { pushes: [], replaces: [], search: '' };
export function useRouter() {
  return {
    push: (url) => { window.__nav.pushes.push(url); },
    replace: (url) => { window.__nav.replaces.push(url); },
    refresh: () => {},
    back: () => {},
    forward: () => {},
    prefetch: () => Promise.resolve(),
  };
}
export function useSearchParams() { return new URLSearchParams(window.__nav.search || ''); }
export function usePathname() { return window.__nav.pathname || '/threat'; }
export function useParams() { return {}; }
export function redirect() {}
export function notFound() {}
`;

const SHIMS: Record<string, string> = {
  '/vendor/react.js': REACT_SHIM,
  '/vendor/react-dom.js': REACT_DOM_SHIM,
  '/vendor/react-dom-client.js': REACT_DOM_CLIENT_SHIM,
  '/vendor/jsx-runtime.js': JSX_RUNTIME_SHIM,
  '/vendor/next-link.js': NEXT_LINK_SHIM,
  '/vendor/next-image.js': NEXT_IMAGE_SHIM,
  '/vendor/next-navigation.js': NEXT_NAVIGATION_SHIM,
};

export type Harness = {
  url: string;
  close: () => Promise<void>;
};

/**
 * Serve the app sources plus a bootstrap page.
 *
 * `bootstrap` is module source evaluated in the browser after React is on
 * `window`; it imports from `/app/...` and mounts whatever the spec is
 * asserting on.
 */
export async function startRenderHarness(options: { bootstrap: string; css?: boolean } = { bootstrap: '' }): Promise<Harness> {
  const appCss = options.css === false ? '' : fs.readFileSync(path.join(APP_DIR, 'styles.css'), 'utf-8');
  const reactUmd = fs.readFileSync(path.join(NODE_MODULES, 'react', 'umd', 'react.development.js'), 'utf-8');
  const reactDomUmd = fs.readFileSync(path.join(NODE_MODULES, 'react-dom', 'umd', 'react-dom.development.js'), 'utf-8');

  const page = `<!doctype html>
<html><head><meta charset="utf-8"><title>render harness</title><style>${appCss}</style></head>
<body><div id="root"></div>
<script>${reactUmd}</script>
<script>${reactDomUmd}</script>
<script>
// Classic script: installs the error sinks BEFORE any module is evaluated, so a
// parse error, a failed module resolution or a component that throws during
// render all surface as window.__renderError instead of a silently blank page.
window.__renderError = null;
window.addEventListener('error', (e) => {
  window.__renderError = String((e.error && e.error.stack) || e.message || e.error);
});
window.addEventListener('unhandledrejection', (e) => {
  window.__renderError = String((e.reason && e.reason.stack) || e.reason);
});
</script>
<script type="module">
// Module scope: import declarations must stay at the top level, so this block
// is NOT wrapped in a try/catch — the listeners above are what catch it.
${options.bootstrap}
</script>
</body></html>`;

  const server = http.createServer((req, res) => {
    const url = (req.url || '/').split('?')[0];
    try {
      if (url === '/' || url === '/index.html') {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
        res.end(page);
        return;
      }
      const shim = SHIMS[url];
      if (shim) {
        res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' });
        res.end(shim);
        return;
      }
      if (url.startsWith('/app/')) {
        const file = path.join(APP_DIR, decodeURIComponent(url.slice('/app/'.length)));
        // Path containment: a served module must live inside apps/web/app.
        if (!file.startsWith(APP_DIR) || !fs.existsSync(file)) {
          res.writeHead(404).end('not found');
          return;
        }
        // Transpile BEFORE the headers go out, so a resolution failure can
        // still be answered with a module that throws the real reason.
        const compiled = transpile(file);
        res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' });
        res.end(compiled);
        return;
      }
      res.writeHead(404).end('not found');
    } catch (err) {
      // Surface a transpile/resolution failure as a JS module that throws, so
      // the spec fails with the real reason instead of a blank page.
      const message = JSON.stringify(String((err as Error).message || err));
      if (!res.headersSent) res.writeHead(500, { 'content-type': 'text/javascript; charset=utf-8' });
      res.end(`throw new Error(${message});`);
    }
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  return {
    url: `http://127.0.0.1:${port}/`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

/** The Chromium actually present in this environment (build number may differ). */
export function resolveChromium(): string | undefined {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    const dir = fs
      .readdirSync(base)
      .find((d) => d.startsWith('chromium-') && fs.existsSync(path.join(base, d, 'chrome-linux', 'chrome')));
    if (dir) return path.join(base, dir, 'chrome-linux', 'chrome');
  } catch {
    /* fall through to the Playwright default */
  }
  return undefined;
}

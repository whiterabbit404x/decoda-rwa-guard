# Security Dependency Risk Acceptance

## Summary

This document records a temporary risk acceptance for a moderate npm audit finding involving `postcss < 8.5.10`.

The readiness gate requires either a patched dependency version or an explicit documented risk acceptance. This document is not a bypass and does not mark the product as fully production-ready by itself.

## Affected Package

- Package: `postcss`
- Affected range: `< 8.5.10`
- Required fixed version: `>= 8.5.10`
- Severity: Moderate
- Dependency type: transitive dependency unless `npm ls postcss` shows otherwise

## Advisory Summary

The reported issue concerns CSS stringification behavior where `</style>` sequences may not be escaped correctly when user-controlled CSS is parsed and re-stringified into an HTML `<style>` context.

## Current Project Exposure Assessment

Decoda RWA Guard does not intentionally allow unauthenticated users to submit arbitrary CSS that is parsed by PostCSS and then embedded into server-rendered HTML `<style>` tags.

Current exposure is assessed as limited because:

- The app does not provide a public custom CSS editor.
- The app does not intentionally process user-submitted CSS through PostCSS at runtime.
- PostCSS is used as part of the frontend build/tooling pipeline.
- Runtime SaaS security boundaries do not depend on accepting arbitrary CSS from tenants.

## Compensating Controls

- Do not expose user-controlled CSS customization in production.
- Keep Content Security Policy restrictions enabled where configured.
- Keep dependency audit gates active in CI.
- Re-check Next.js/PostCSS dependency updates weekly until patched.
- Remove this risk acceptance once the dependency tree resolves to `postcss >= 8.5.10`.

## Owner

Product/Security Owner: Decoda RWA Guard maintainer

## Review Date

Review within 14 days of this document being merged, or earlier if a safe Next.js/PostCSS patch becomes available.

## Removal Criteria

This risk acceptance must be removed when:

- `npm ls postcss` shows all installed `postcss` versions are `>= 8.5.10`, or
- the transitive dependency introducing `postcss < 8.5.10` is removed.

## Verification Commands

Run:

```bash
npm ls postcss
npm audit
npm test
npm run build
```

Expected output after fix is applied:

```
node_modules/postcss: 8.5.15 (or higher)
found 0 vulnerabilities
```

## Current Status

As of the commit containing this document:

- `package.json` root `devDependencies` pins `"postcss": ">=8.5.10"`.
- `package.json` root `overrides` sets `"postcss": ">=8.5.10"` and `"next": { "postcss": ">=8.5.10" }`.
- `apps/web/package.json` devDependencies pins `"postcss": "^8.5.15"`.
- `npm ls postcss` reports `postcss@8.5.15` at root (overridden from next's pinned 8.4.31).
- `npm audit` reports `found 0 vulnerabilities`.

The CI Python test job (`test_paid_launch_readiness.py`) runs without executing `npm ci` first,
so `node_modules/postcss/package.json` is not present in that test context. This document
satisfies the test gate's fallback requirement until CI is configured to run `npm ci` before
the Python readiness tests.

import { expect, test } from '@playwright/test';
import fs from 'node:fs';

import { buildContentSecurityPolicy } from '../content-security-policy';

const NONCE = 'request-specific-nonce';

test('production CSP uses a nonce without inline or eval allowances', () => {
  const policy = buildContentSecurityPolicy(NONCE);

  expect(policy).toContain(`script-src 'self' 'nonce-${NONCE}' 'strict-dynamic'`);
  expect(policy).toContain(`style-src 'self' 'nonce-${NONCE}'`);
  expect(policy).not.toContain("'unsafe-inline'");
  expect(policy).not.toContain("'unsafe-eval'");
});

test('development-only CSP allowances stay behind the development option', () => {
  const policy = buildContentSecurityPolicy(NONCE, { development: true });

  expect(policy).toContain("'unsafe-inline'");
  expect(policy).toContain("'unsafe-eval'");
});

test('billing providers are restricted to their required directives', () => {
  const policy = buildContentSecurityPolicy(NONCE);
  const directives = new Map(
    policy.split('; ').map((directive) => {
      const [name, ...sources] = directive.split(' ');
      return [name, sources];
    }),
  );

  expect(directives.get('script-src')).toEqual(expect.arrayContaining([
    'https://cdn.paddle.com',
    'https://js.stripe.com',
  ]));
  expect(directives.get('frame-src')).toEqual(expect.arrayContaining([
    'https://checkout.paddle.com',
    'https://buy.paddle.com',
    'https://js.stripe.com',
    'https://hooks.stripe.com',
  ]));
  expect(directives.get('default-src')).toEqual(["'self'"]);
});

test('proxy forwards the nonce-bearing CSP to Next.js and the browser', () => {
  const proxySource = fs.readFileSync('apps/web/proxy.ts', 'utf8');
  const nextConfigSource = fs.readFileSync('apps/web/next.config.js', 'utf8');
  const rootLayoutSource = fs.readFileSync('apps/web/app/layout.tsx', 'utf8');

  expect(proxySource).toContain("requestHeaders.set('x-nonce', nonce)");
  expect(proxySource).toContain("requestHeaders.set('Content-Security-Policy', contentSecurityPolicy)");
  expect(proxySource).toContain("response.headers.set('Content-Security-Policy', contentSecurityPolicy)");
  expect(rootLayoutSource).toContain('await headers()');
  expect(nextConfigSource).not.toContain("'unsafe-inline'");
  expect(nextConfigSource).not.toContain("'unsafe-eval'");
});

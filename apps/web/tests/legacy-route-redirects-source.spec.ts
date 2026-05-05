import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

function productSource(relativePath: string): string {
  return readFileSync(path.join(__dirname, '..', 'app', '(product)', relativePath), 'utf8');
}

test('keeps legacy exports and resilience routes as explicit redirects to canonical destinations', () => {
  const exportsRedirectSource = productSource('exports/page.tsx');
  expect(exportsRedirectSource).toContain("import { redirect } from 'next/navigation';");
  expect(exportsRedirectSource).toContain("redirect('/evidence');");

  const resilienceRedirectSource = productSource('resilience/page.tsx');
  expect(resilienceRedirectSource).toContain("import { redirect } from 'next/navigation';");
  expect(resilienceRedirectSource).toContain("redirect('/system-health');");
});

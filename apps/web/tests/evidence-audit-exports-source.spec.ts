import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

test('exports page uses evidence and audit terminology with expected tabs and headers', () => {
  const source = read('app/(product)/exports/page.tsx');

  expect(source).toContain('Evidence &amp; Audit');
  expect(source).toContain("label: 'Evidence Packages'");
  expect(source).toContain("label: 'Audit Logs'");
  expect(source).toContain("['Package ID', 'Incident', 'Date Created', 'Includes', 'Size', 'Evidence Source', 'Actions']");
});

test('exports page disables export and download actions when package artifact is absent', () => {
  const source = read('app/(product)/exports/page.tsx');

  expect(source).toContain('const ready = Boolean(job.download_url || job.package_ready);');
  expect(source).toContain('disabled={!row.ready}');
  expect(source).toContain('if (!row.ready) event.preventDefault();');
});

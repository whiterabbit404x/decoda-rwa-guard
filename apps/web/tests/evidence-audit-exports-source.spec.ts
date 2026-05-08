  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

test('evidence page uses Evidence & Audit terminology with expected tabs and headers', () => {
  const source = read('app/evidence-audit-panel.tsx');

  expect(source).toContain('Evidence &amp; Audit');
  expect(source).toContain("label: 'Evidence Packages'");
  expect(source).toContain("label: 'Audit Logs'");
  expect(source).toContain("'Package ID'");
  expect(source).toContain("'Incident'");
  expect(source).toContain("'Date Created'");
  expect(source).toContain("'Includes'");
  expect(source).toContain("'Size'");
  expect(source).toContain("'Evidence Source'");
  expect(source).toContain("'Actions'");
});

test('evidence page disables export and download actions when package artifact is absent', () => {
  const source = read('app/evidence-audit-panel.tsx');

  expect(source).toContain('isPackageReady');
  expect(source).toContain('disabled={!ready}');
});

test('exports page redirects to /evidence', () => {
  const source = read('app/(product)/exports/page.tsx');
  expect(source).toContain("redirect('/evidence')");
});

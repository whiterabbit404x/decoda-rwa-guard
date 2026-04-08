import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('assets manager includes explicit validation and clear create states', () => {
  const assets = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

  expect(assets).toContain('Asset name is required.');
  expect(assets).toContain('Wallet address / identifier is required.');
  expect(assets).toContain('Enter a valid Ethereum address.');
  expect(assets).toContain('Create asset is disabled until required fields are valid.');
  expect(assets).toContain('Creating asset…');
  expect(assets).toContain('focusFirstInvalid');
  expect(assets).toContain('scrollIntoView');
  expect(assets).toContain('Asset created successfully.');
  expect(assets).toContain('Asset create failed');
  expect(assets).toContain('classifyApiTransportError');
});

test('assets manager presets keep required fields visible and guide name entry', () => {
  const assets = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

  expect(assets).toContain('Ethereum Wallet');
  expect(assets).toContain('Smart Contract');
  expect(assets).toContain('Treasury Vault');
  expect(assets).toContain('fieldRefs.current.name?.focus()');
  expect(assets).toContain('requiredMark');
  expect(assets).toContain('Example wallet: 0x5f6f35FD8b10C5576089f99C7c8c351Deb851d1F');
});

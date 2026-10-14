import { expect, test } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

const PNG_SIGNATURE = '89504e470d0a1a0a';
const PNG_COLOR_TYPE_RGBA = 6;

function readIcoEntries(buffer: Buffer) {
  // ICONDIR: reserved (0), type (1 = icon), count.
  expect(buffer.readUInt16LE(0)).toBe(0);
  expect(buffer.readUInt16LE(2)).toBe(1);
  const count = buffer.readUInt16LE(4);
  return Array.from({ length: count }, (_, index) => {
    const offset = 6 + index * 16;
    const width = buffer.readUInt8(offset) || 256;
    const height = buffer.readUInt8(offset + 1) || 256;
    const size = buffer.readUInt32LE(offset + 8);
    const dataOffset = buffer.readUInt32LE(offset + 12);
    return { width, height, data: buffer.subarray(dataOffset, dataOffset + size) };
  });
}

test('app/favicon.ico ships 16x16 and 32x32 icons with an alpha channel', async () => {
  const buffer = await fs.readFile(path.resolve(process.cwd(), 'app/favicon.ico'));
  const entries = readIcoEntries(buffer);
  const sizes = entries.map((entry) => `${entry.width}x${entry.height}`);

  expect(sizes).toEqual(expect.arrayContaining(['16x16', '32x32']));
  for (const entry of entries) {
    // Transparent background requires RGBA PNG payloads (no opaque square).
    expect(entry.data.subarray(0, 8).toString('hex')).toBe(PNG_SIGNATURE);
    expect(entry.data.readUInt8(25)).toBe(PNG_COLOR_TYPE_RGBA);
  }
});

test('app/apple-icon.png is a 180x180 RGBA png', async () => {
  const buffer = await fs.readFile(path.resolve(process.cwd(), 'app/apple-icon.png'));

  expect(buffer.subarray(0, 8).toString('hex')).toBe(PNG_SIGNATURE);
  expect(buffer.readUInt32BE(16)).toBe(180);
  expect(buffer.readUInt32BE(20)).toBe(180);
  expect(buffer.readUInt8(25)).toBe(PNG_COLOR_TYPE_RGBA);
});

test('proxy matcher keeps serving favicon.ico without per-request CSP work', async () => {
  const source = await fs.readFile(path.resolve(process.cwd(), 'proxy.ts'), 'utf8');

  expect(source).toContain('favicon.ico');
});

// ── The canonical Decoda shield ─────────────────────────────────────────────
// The tab icon is the company website's own mark (www.decodasecurity.com
// app/icon.svg), not an approximation: the same path, check and gradient. The
// header and footer lockups draw the same geometry inline.

const CANONICAL_SHIELD =
  'M20 3.2 33.4 8.1c.7.26 1.2.94 1.2 1.7v9.3c0 8.1-5.1 15.3-12.9 18.1l-1.1.4c-.4.14-.8.14-1.2 0l-1.1-.4C10.5 34.5 5.4 27.3 5.4 19.2V9.8c0-.76.5-1.44 1.2-1.7L20 3.2Z';
const CANONICAL_CHECK = 'M14 20.4 18.2 24.6 26.6 15.8';

test('app/icon.svg is the canonical Decoda shield', async () => {
  const svg = await fs.readFile(path.resolve(process.cwd(), 'app/icon.svg'), 'utf8');
  expect(svg).toContain(`d="${CANONICAL_SHIELD}"`);
  expect(svg).toContain(`d="${CANONICAL_CHECK}"`);
  // Navy body, teal-to-cyan edge, light teal check.
  expect(svg).toContain('fill="#0b1d38"');
  expect(svg).toContain('stop-color="#2dd4bf"');
  expect(svg).toContain('stop-color="#0e7490"');
  expect(svg).toContain('stroke="#5eead4"');
});

test('the inline logo draws the same geometry as the tab icon', async () => {
  const logo = await fs.readFile(path.resolve(process.cwd(), 'app/components/home/decoda-logo.tsx'), 'utf8');
  expect(logo).toContain(`'${CANONICAL_SHIELD}'`);
  expect(logo).toContain(`'${CANONICAL_CHECK}'`);
});

test('the legacy blue shield is gone from the public pages', async () => {
  // The old approximation: a filled blue 26×28 shield.
  const legacy = 'M13 1.5L2 6.5V14c0 6.2';
  for (const file of [
    'app/page.tsx',
    'app/request-pilot/page.tsx',
    'app/request-pilot/request-pilot-view.tsx',
    'app/components/home/decoda-logo.tsx',
    'app/components/home/marketing-header.tsx',
    'app/components/home/marketing-footer.tsx',
  ]) {
    const source = await fs.readFile(path.resolve(process.cwd(), file), 'utf8');
    expect(source, `${file} still draws the legacy shield`).not.toContain(legacy);
  }
});

test('favicon.ico also carries a 48x48 icon for high-density tabs', async () => {
  const buffer = await fs.readFile(path.resolve(process.cwd(), 'app/favicon.ico'));
  const sizes = readIcoEntries(buffer).map((entry) => `${entry.width}x${entry.height}`);
  expect(sizes).toEqual(expect.arrayContaining(['16x16', '32x32', '48x48']));
});

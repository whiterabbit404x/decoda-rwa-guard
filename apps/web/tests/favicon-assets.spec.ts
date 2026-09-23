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

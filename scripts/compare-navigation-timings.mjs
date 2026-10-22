#!/usr/bin/env node
/**
 * Compare two navigation-timing captures (scripts/capture-navigation-timings.mjs)
 * and print Markdown tables of before → after medians, with the change.
 *
 * Measurement reporting only; reads the two JSON artifacts, writes stdout.
 *
 *   node scripts/compare-navigation-timings.mjs \
 *     artifacts/navigation-timing/before-rtt150/navigation-timings.json \
 *     artifacts/navigation-timing/after-rtt150/navigation-timings.json
 */

import * as fs from 'node:fs';

function load(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function cell(before, after) {
  if (typeof before !== 'number' || typeof after !== 'number') return `${before ?? '—'} → ${after ?? '—'}`;
  const delta = after - before;
  const sign = delta > 0 ? '+' : '';
  return `${Math.round(before)} → ${Math.round(after)} (${sign}${Math.round(delta)})`;
}

export function compare(before, after) {
  const lines = [];
  lines.push(`Before: ${before.capturedAt}. After: ${after.capturedAt}. ${after.loads} loads per screen, medians in ms; added round-trip latency ${after.latencyMs}ms.`);
  lines.push('');
  lines.push('### Document loads');
  lines.push('');
  lines.push('| screen | auth gate open | ready (no loading indicator) | data settled | API requests |');
  lines.push('|---|---:|---:|---:|---:|');
  for (const a of after.documentSummary) {
    const b = before.documentSummary.find((row) => row.route === a.route) ?? {};
    lines.push(`| ${a.route} | ${cell(b.gate, a.gate)} | ${cell(b.ready, a.ready)} | ${cell(b.settled, a.settled)} | ${cell(b.api, a.api)} |`);
  }
  lines.push('');
  lines.push('### Sidebar navigation');
  lines.push('');
  lines.push('| screen | URL commit | ready | data settled | API requests |');
  lines.push('|---|---:|---:|---:|---:|');
  for (const a of after.sidebarSummary) {
    const b = before.sidebarSummary.find((row) => row.route === a.route) ?? {};
    lines.push(`| ${a.route} | ${cell(b.urlCommit, a.urlCommit)} | ${cell(b.ready, a.ready)} | ${cell(b.settled, a.settled)} | ${cell(b.api, a.api)} |`);
  }
  const steadyBefore = Array.isArray(before.steady) ? before.steady : [before.steady];
  const steadyAfter = Array.isArray(after.steady) ? after.steady : [after.steady];
  for (const a of steadyAfter) {
    const b = steadyBefore.find((row) => row?.route === a.route);
    if (!b || !a.seconds) continue;
    lines.push('');
    lines.push(`### Steady state: ${a.seconds}s on ${a.route}`);
    lines.push('');
    lines.push('| path | before | after |');
    lines.push('|---|---:|---:|');
    const paths = [...new Set([...Object.keys(b.byPath), ...Object.keys(a.byPath)])].sort();
    for (const p of paths) lines.push(`| ${p} | ${b.byPath[p] ?? 0} | ${a.byPath[p] ?? 0} |`);
    lines.push(`| **total** | **${b.apiCount}** | **${a.apiCount}** |`);
  }
  return lines.join('\n');
}

const invokedDirectly = process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop());
if (invokedDirectly) {
  const [beforeFile, afterFile] = process.argv.slice(2);
  if (!beforeFile || !afterFile) {
    process.stderr.write('usage: compare-navigation-timings.mjs <before.json> <after.json>\n');
    process.exit(2);
  }
  process.stdout.write(`${compare(load(beforeFile), load(afterFile))}\n`);
}
